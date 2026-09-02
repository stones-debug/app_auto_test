<script setup lang="ts">
import type { Step } from '@/api/cases'
import CaseStepEditor from '@/components/CaseStepEditor.vue'

defineProps<{
  projectId?: number
  setupSteps: Step[]
  teardownSteps: Step[]
  dirty: boolean
  saving: boolean
}>()

const emit = defineEmits<{
  'update:setup-steps': [steps: Step[]]
  'update:teardown-steps': [steps: Step[]]
  save: []
  discard: []
}>()
</script>

<template>
  <section class="detail-section">
    <header class="section-head">
      <div class="section-title-wrap">
        <span class="section-accent accent-amber"></span>
        <div>
          <div class="v2-card-title">执行步骤</div>
          <div class="v2-aux">套件级前置 / 后置操作，点击「保存」后生效</div>
        </div>
      </div>
      <div class="section-head-right">
        <el-tag v-if="!dirty" size="small" effect="plain" type="info">需手动保存</el-tag>
        <el-tag v-else size="small" effect="light" type="warning">未保存</el-tag>
      </div>
    </header>

    <div class="steps-grid">
      <CaseStepEditor
        :model-value="setupSteps"
        @update:model-value="emit('update:setup-steps', $event)"
        phase="setup"
        :project-id="projectId"
        title="前置操作"
        description="运行时在套件内每个用例主体之前执行"
        tone="warning"
        :allow-assertions="false"
      />
      <CaseStepEditor
        :model-value="teardownSteps"
        @update:model-value="emit('update:teardown-steps', $event)"
        phase="teardown"
        :project-id="projectId"
        title="后置操作"
        description="运行时在套件内每个用例完成后执行；失败时仍会尝试清理"
        tone="success"
        :allow-assertions="false"
      />
    </div>

    <transition name="fade">
      <div v-if="dirty" class="steps-save-bar">
        <div class="save-hint"><span class="save-dot"></span>有未保存的步骤改动</div>
        <div class="save-actions">
          <el-button @click="emit('discard')">放弃改动</el-button>
          <el-button type="primary" :loading="saving" @click="emit('save')">保存套件配置</el-button>
        </div>
      </div>
    </transition>
  </section>
</template>

<style scoped>
.detail-section {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  margin-bottom: 16px;
  overflow: visible;
}
.section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
}
.section-title-wrap {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-width: 0;
}
.section-accent {
  width: 4px;
  height: 18px;
  border-radius: 2px;
  flex-shrink: 0;
  margin-top: 2px;
}
.accent-amber { background: var(--warning); }
.section-head-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
.steps-grid {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px 20px 0;
}
.steps-save-bar {
  position: sticky;
  bottom: 0;
  margin-top: 16px;
  padding: 12px 20px;
  background: var(--card-bg);
  border-top: 1px solid var(--border);
  box-shadow: 0 -4px 16px rgba(15, 23, 42, 0.06);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.save-hint, .save-actions { display: flex; align-items: center; gap: 8px; }
.save-hint { color: var(--warning); font-size: 13px; }
.save-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--warning);
  box-shadow: 0 0 0 3px rgba(245, 158, 11, 0.2);
}
</style>
