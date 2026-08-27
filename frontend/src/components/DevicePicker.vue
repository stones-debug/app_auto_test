<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { getExecution, type Execution, type ExecutionRunSettings } from '@/api/executions'
import { apiErrorCode, buildRunParameters, useDeviceSelect, type ProfileRunContext, type RunTarget } from '@/composables/useDeviceSelect'
import { getAppProfile, listReleases, previewExecution, type ExecutionPreview } from '@/api/appProfiles'
import { useAppProfileStore } from '@/stores/appProfile'
import { ElMessage } from 'element-plus'
import type { Device } from '@/api/agents'

// V2 §5.12：统一设备选择器（所有运行入口复用，含重试入口）。
// 方案 §4.8/§5.7：公共库运行必须选档案+版本；档案视图带入则只读显示。选择后调用预检展示摘要。
const emit = defineEmits<{ created: [execution: Execution] }>()

const props = defineProps<{ projectId?: number }>()

/** 设备选项文本：名称 + 平台 + 序列号/地址，同一电脑多设备可区分。 */
function deviceOptionLabel(d: Device): string {
  const ident = d.udid || d.address
  const parts = [`${d.name} (${d.platform})`]
  if (ident) parts.push(ident)
  return parts.join(' · ')
}

const timeout = ref(1800)
const usePreSteps = ref(false)
const usePostSteps = ref(false)
const attachToCurrentApp = ref(false)
const store = useAppProfileStore()
const { dialogVisible, devices, selectedId, setAsDefault, running, reason, targetKind, open, confirmRun, close } =
  useDeviceSelect()

const profileId = ref<number | null>(null)
const releaseId = ref<number | null>(null)
const releases = ref<{ id: number; version: string }[]>([])
const preview = ref<ExecutionPreview | null>(null)
const previewLoading = ref(false)
const profileReadonly = ref(false)
const retryRevisionNotice = ref('')
let previewSequence = 0

let targetIdsTracker: number[] = []

/** 弹窗路径的 open() 挂起解析器（成功/取消时唤醒父级 await）。 */
let openResolve: ((exec: Execution | null) => void) | null = null

function settleOpen(exec: Execution | null) {
  openResolve?.(exec)
  openResolve = null
}

async function run() {
  const parameters = buildRunParameters(targetKind.value ?? 'retry', {
    use_pre_steps: usePreSteps.value,
    use_post_steps: usePostSteps.value,
    attach_to_current_app: attachToCurrentApp.value,
  })
  // 方案 §4.8：合并档案上下文
  const profile: ProfileRunContext | undefined = profileId.value != null && releaseId.value != null && preview.value
    ? {
        app_profile_id: profileId.value,
        app_release_id: releaseId.value,
        expected_profile_revision: preview.value.profile_revision,
        expected_test_asset_revision: preview.value.test_asset_revision,
      }
    : undefined
  try {
    const exec = await confirmRun({ timeout_seconds: timeout.value, parameters, profile })
    if (exec) {
      settleOpen(exec)
      emit('created', exec)
    }
  } catch (error) {
    if (['PROFILE_REVISION_CONFLICT', 'TEST_ASSET_REVISION_CONFLICT', 'APP_RELEASE_CHANGED'].includes(apiErrorCode(error) ?? '')) {
      ElMessage.warning('配置已变化，已重新预检，请确认后再次运行')
      await doPreview()
      return
    }
    ElMessage.error((error as Error).message || '创建执行失败')
  }
}

async function onProfileChange(id: number) {
  profileId.value = id
  releaseId.value = null
  releases.value = []
  preview.value = null
  const page = await listReleases(id, { status: 'active', page_size: 100 })
  releases.value = page.items.map((r) => ({ id: r.id, version: r.version }))
  const remembered = recentRelease(id)
  if (remembered != null && releases.value.some((release) => release.id === remembered)) releaseId.value = remembered
  else if (releases.value.length > 0) releaseId.value = releases.value[0].id
  await doPreview()
}

async function onReleaseChange() {
  rememberRelease()
  await doPreview()
}

function recentRelease(id: number): number | null {
  if (typeof localStorage === 'undefined') return null
  const value = Number(localStorage.getItem(`app-profile-release:${id}`))
  return Number.isFinite(value) && value > 0 ? value : null
}

function rememberRelease() {
  if (typeof localStorage !== 'undefined' && profileId.value != null && releaseId.value != null) {
    localStorage.setItem(`app-profile-release:${profileId.value}`, String(releaseId.value))
  }
}

function runParameters(): Record<string, unknown> {
  return buildRunParameters(targetKind.value ?? 'retry', {
    use_pre_steps: usePreSteps.value,
    use_post_steps: usePostSteps.value,
    attach_to_current_app: attachToCurrentApp.value,
  })
}

async function doPreview() {
  if (profileId.value == null || releaseId.value == null) {
    preview.value = null
    return
  }
  if (targetKind.value === 'retry') return
  const sequence = ++previewSequence
  previewLoading.value = true
  try {
    const result = await previewExecution({
      project_id: props.projectId ?? 0,
      target: {
        type: targetKind.value === 'batch' ? 'batch' : (targetKind.value === 'suite' ? 'suite' : 'case'),
        ids: targetIdsTracker,
      },
      app_profile_id: profileId.value,
      app_release_id: releaseId.value,
      device_id: selectedId.value,
      parameters: runParameters(),
    })
    if (sequence === previewSequence) {
      preview.value = result
      store.markRevision(result.profile_revision, result.test_asset_revision)
    }
  } catch (e) {
    if (sequence === previewSequence) {
      preview.value = null
      ElMessage.warning((e as Error).message)
    }
  } finally {
    if (sequence === previewSequence) previewLoading.value = false
  }
}

const hasBlockingWarning = computed(() => (preview.value?.warnings ?? []).some((warning) => {
  const item = warning as { blocking?: boolean; severity?: string }
  return item.blocking === true || item.severity === 'error'
}))

const runDisabled = computed(() => {
  if (selectedId.value == null || running.value) return true
  if (targetKind.value === 'retry') return false
  return profileId.value == null
    || releaseId.value == null
    || previewLoading.value
    || preview.value == null
    || preview.value.counts.executable_cases < 1
    || hasBlockingWarning.value
})

function cancel() {
  settleOpen(null)
  close()
}

function onClosed() {
  // 弹窗被关闭（取消/遮罩/X/运行成功后的收起）→ 挂起的 open() 以 null 结束
  settleOpen(null)
  close()
}

defineExpose({
  /**
   * 始终弹出设备选择弹窗（预选用户默认设备）；
   * 运行成功 resolve Execution、取消 resolve null。
   * options.profile：档案视图带入（只读）；未提供则在弹窗内选择档案+版本。
   */
  open: async (
    target: RunTarget,
    options: { timeout_seconds?: number; settings?: Partial<ExecutionRunSettings>; profile?: ProfileRunContext; targetId?: number } = {},
  ): Promise<Execution | null> => {
    if (options.timeout_seconds) timeout.value = options.timeout_seconds
    usePreSteps.value = options.settings?.use_pre_steps ?? false
    usePostSteps.value = options.settings?.use_post_steps ?? false
    attachToCurrentApp.value = options.settings?.attach_to_current_app ?? false
    // 档案视图带入 → 只读；否则弹出选择
    if (options.profile) {
      profileId.value = options.profile.app_profile_id
      releaseId.value = options.profile.app_release_id
      profileReadonly.value = true
    } else {
      profileReadonly.value = false
      profileId.value = null
      releaseId.value = null
      releases.value = []
    }
    retryRevisionNotice.value = ''
    if (target.kind === 'retry') {
      const original = await getExecution(target.executionId)
      if (original.app_profile_id != null) {
        const current = await getAppProfile(original.app_profile_id)
        retryRevisionNotice.value = [
          `原执行：${original.app_profile_name_snapshot ?? `档案 #${original.app_profile_id}`} / ${original.app_release_version_snapshot ?? '未标注版本'}`,
          `配置 revision ${original.profile_revision ?? '-'} → 当前 ${current.revision}`,
          `资产 revision ${original.test_asset_revision ?? '-'}。重试将按当前配置重新生成快照。`,
        ].join('；')
      } else {
        retryRevisionNotice.value = '这是历史兼容执行，重试将继续使用原始公共测试资产流程。'
      }
    }
    if (target.kind === 'batch') targetIdsTracker = target.suiteIds
    else if (options.targetId) targetIdsTracker = [options.targetId]
    else if (target.kind === 'case' || target.kind === 'suite') targetIdsTracker = [target.id]
    else targetIdsTracker = []
    store.projectId = props.projectId ?? store.projectId
    if (props.projectId != null) {
      await store.loadProfiles()
    }
    if (profileReadonly.value && profileId.value != null) {
      const page = await listReleases(profileId.value, { status: 'active', page_size: 100 })
      releases.value = page.items.map((release) => ({ id: release.id, version: release.version }))
    }
    return new Promise((resolve) => {
      openResolve = resolve
      open(target).then((exec) => {
        if (exec !== null) {
          openResolve = null
          resolve(exec)
        }
        if (target.kind !== 'retry') void doPreview()
      })
    })
  },
  close,
})

watch([selectedId, usePreSteps, usePostSteps, attachToCurrentApp], () => {
  if (dialogVisible.value && targetKind.value !== 'retry') void doPreview()
})
</script>

<template>
  <el-dialog
    v-model="dialogVisible"
    title="选择设备运行"
    width="460px"
    :close-on-click-modal="false"
    append-to-body
    @closed="onClosed"
  >
    <el-form label-width="80px">
      <el-alert v-if="targetKind === 'retry' && retryRevisionNotice" type="warning" :closable="false" show-icon :title="retryRevisionNotice" class="retry-notice" />
      <el-form-item v-if="targetKind !== 'retry'" label="APP 档案">
        <template v-if="profileReadonly && profileId">
          <el-tag size="small">{{ store.currentProfile?.name ?? `#${profileId}` }}</el-tag>
        </template>
        <template v-else>
          <el-select v-model="profileId" class="w-full" placeholder="选择 APP 档案" @change="onProfileChange">
            <el-option v-for="p in store.profiles" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
        </template>
      </el-form-item>
      <el-form-item v-if="targetKind !== 'retry'" label="发布版本">
        <el-select v-model="releaseId" class="w-full" placeholder="选择版本" :disabled="!profileId || profileReadonly" @change="onReleaseChange">
          <el-option v-for="r in releases" :key="r.id" :label="r.version" :value="r.id" />
        </el-select>
      </el-form-item>
      <el-form-item v-if="targetKind !== 'retry'" label="预检">
        <div v-if="previewLoading" class="preview-block">正在解析最终执行内容…</div>
        <div v-else-if="preview" class="preview-block">
          源 {{ preview.counts.source_cases }} 用例 → 实际 {{ preview.counts.executable_cases }} 用例 / {{ preview.counts.executable_steps }} 步
          · N/A {{ preview.counts.na_cases + preview.counts.na_steps + preview.counts.na_assertions }} · 覆盖 {{ preview.counts.overrides }}
          <div v-if="hasBlockingWarning" class="tip warn">预检存在阻断项，暂不能运行。</div>
        </div>
        <div v-else class="tip warn">请选择档案和发布版本，并等待预检成功。</div>
      </el-form-item>
      <el-form-item label="设备">
        <el-select v-model="selectedId" placeholder="选择设备" class="w-full">
          <el-option
            v-for="d in devices"
            :key="d.id"
            :label="deviceOptionLabel(d)"
            :value="d.id"
          />
        </el-select>
        <div v-if="reason" class="tip warn">{{ reason }}</div>
        <div v-if="!devices.length" class="tip">
          当前没有可用的在线空闲设备。请先在「设备中心」下载安装 Agent 并绑定。
        </div>
      </el-form-item>
      <el-form-item label="设为默认">
        <el-checkbox v-model="setAsDefault">运行后记住此设备</el-checkbox>
      </el-form-item>
      <el-form-item label="超时(s)">
        <el-input-number v-model="timeout" :min="60" :max="7200" :step="60" />
      </el-form-item>
      <template v-if="targetKind === 'case' || targetKind === 'suite' || targetKind === 'batch'">
        <el-divider content-position="left">执行选项</el-divider>
        <el-form-item label="用例阶段">
          <div class="option-list">
            <el-checkbox v-model="usePreSteps">执行用例前置操作</el-checkbox>
            <el-checkbox v-model="usePostSteps">执行用例后置操作</el-checkbox>
          </div>
          <div class="tip">套件运行时，每个用例分别应用其自身的前置和后置操作。</div>
        </el-form-item>
        <el-form-item v-if="targetKind === 'case'" label="启动方式">
          <el-checkbox v-model="attachToCurrentApp">复用设备当前界面，不启动 APP</el-checkbox>
          <div class="tip">Agent 将建立当前界面会话，并跳过用例中的“启动 APP”步骤。</div>
        </el-form-item>
      </template>
    </el-form>
    <template #footer>
      <el-button @click="cancel">取消</el-button>
      <el-button type="primary" :loading="running" :disabled="runDisabled" @click="run">运行</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.w-full {
  width: 100%;
}
.preview-block {
  font-size: 12px;
  color: #409eff;
  line-height: 1.6;
}
.tip {
  color: #909399;
  font-size: 12px;
  margin-top: 6px;
  line-height: 1.7;
}
.tip.warn {
  color: #e6a23c;
}
.option-list {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}
.retry-notice { margin-bottom: 14px; }
</style>
