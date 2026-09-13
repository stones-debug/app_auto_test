import { ref } from 'vue'

import { getSuite, listVariables, updateSuite, updateVariable, deleteVariable, type Suite, type Variable } from '@/api/suites'
import { normalizeStep, validateStep, type Step } from '@/api/cases'
import { useUnsavedChanges } from '@/composables/useUnsavedChanges'
import { toSuiteStepPayload } from '@/utils/suiteSteps'
import { buildVariableValueUpdate, variableEditSeed } from '@/utils/variableEditing'

export function useSuiteDetail(onSaved?: () => Promise<void>) {
  const loadingDetail = ref(false)
  const suiteDetail = ref<Suite | null>(null)
  const setupSteps = ref<Step[]>([])
  const teardownSteps = ref<Step[]>([])
  const savingSteps = ref(false)
  const { dirty, markDirty, markSaved } = useUnsavedChanges()
  const suiteVars = ref<Variable[]>([])
  const varEditing = ref<{ id: number; value: string; is_sensitive: boolean; sensitive_value_changed: boolean } | null>(null)
  const savingVar = ref(false)
  let stepsLoaded = false
  let requestSeq = 0

  function onSetupStepsChange(steps: Step[]) {
    setupSteps.value = steps
    if (stepsLoaded) markDirty()
  }

  function onTeardownStepsChange(steps: Step[]) {
    teardownSteps.value = steps
    if (stepsLoaded) markDirty()
  }

  async function selectSuite(id: number, opts?: { preserveSteps?: boolean }) {
    const seq = ++requestSeq
    loadingDetail.value = true
    try {
      const detail = await getSuite(id)
      if (seq !== requestSeq) return
      if (opts?.preserveSteps) {
        suiteDetail.value = { ...detail, setup_steps: setupSteps.value, teardown_steps: teardownSteps.value }
      } else {
        suiteDetail.value = detail
        setupSteps.value = (detail.setup_steps ?? []).map((step) => normalizeStep({ ...step, phase: 'setup' }))
        teardownSteps.value = (detail.teardown_steps ?? []).map((step) => normalizeStep({ ...step, phase: 'teardown' }))
      }
      const variables = await listVariables({ scope: 'suite', suite_id: id })
      if (seq !== requestSeq) return
      suiteVars.value = variables
      varEditing.value = null
      stepsLoaded = true
    } finally {
      if (seq === requestSeq) loadingDetail.value = false
    }
  }

  async function confirmDiscardSteps(): Promise<boolean> {
    if (!dirty.value) return true
    try {
      await ElMessageBox.confirm(
        '当前套件的步骤尚未保存，切换后这些修改将丢失。',
        '未保存的修改',
        { type: 'warning', confirmButtonText: '仍然切换', cancelButtonText: '留在当前' },
      )
      return true
    } catch {
      return false
    }
  }

  async function saveSuiteSteps(activeSuite: number | null) {
    if (!activeSuite || !suiteDetail.value) return
    const allSteps = [...setupSteps.value, ...teardownSteps.value]
    for (const step of allSteps) {
      const message = validateStep(step)
      if (message) {
        ElMessage.warning(`套件步骤：${message}`)
        return
      }
    }
    savingSteps.value = true
    try {
      const updated = await updateSuite(activeSuite, {
        name: suiteDetail.value.name,
        description: suiteDetail.value.description ?? null,
        setup_steps: toSuiteStepPayload(setupSteps.value),
        teardown_steps: toSuiteStepPayload(teardownSteps.value),
      })
      suiteDetail.value = {
        ...suiteDetail.value,
        ...updated,
        setup_steps: setupSteps.value,
        teardown_steps: teardownSteps.value,
      }
      markSaved()
      ElMessage.success('套件配置已保存')
      await onSaved?.()
    } finally {
      savingSteps.value = false
    }
  }

  function discardSteps() {
    if (!suiteDetail.value) return
    setupSteps.value = (suiteDetail.value.setup_steps ?? []).map((step) => normalizeStep({ ...step, phase: 'setup' }))
    teardownSteps.value = (suiteDetail.value.teardown_steps ?? []).map((step) => normalizeStep({ ...step, phase: 'teardown' }))
    markSaved()
    ElMessage.info('已放弃未保存的步骤改动')
  }

  function startEditVar(variable: Variable) {
    varEditing.value = {
      id: variable.id,
      value: variableEditSeed(variable),
      is_sensitive: variable.is_sensitive,
      sensitive_value_changed: false,
    }
  }

  async function saveVarValue(variable: Variable, activeSuite: number | null) {
    if (!varEditing.value || varEditing.value.id !== variable.id || !activeSuite) return
    savingVar.value = true
    try {
      const valueUpdate = buildVariableValueUpdate(
        variable,
        varEditing.value.value,
        varEditing.value.sensitive_value_changed,
      )
      await updateVariable(variable.id, {
        ...(valueUpdate ?? {}),
        is_sensitive: varEditing.value.is_sensitive,
      })
      varEditing.value = null
      ElMessage.success('变量已更新')
      await selectSuite(activeSuite, { preserveSteps: true })
    } finally {
      savingVar.value = false
    }
  }

  async function removeVar(variable: Variable, activeSuite: number | null) {
    if (!activeSuite) return
    try {
      await ElMessageBox.confirm(`确认删除变量「${variable.name}」？`, '删除变量', {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
      })
    } catch {
      return
    }
    await deleteVariable(variable.id)
    ElMessage.success('变量已删除')
    await selectSuite(activeSuite, { preserveSteps: true })
  }

  function reset() {
    requestSeq += 1
    loadingDetail.value = false
    suiteDetail.value = null
    setupSteps.value = []
    teardownSteps.value = []
    suiteVars.value = []
    varEditing.value = null
    stepsLoaded = false
    markSaved()
  }

  return {
    loadingDetail,
    suiteDetail,
    setupSteps,
    teardownSteps,
    savingSteps,
    dirty,
    suiteVars,
    varEditing,
    savingVar,
    onSetupStepsChange,
    onTeardownStepsChange,
    selectSuite,
    confirmDiscardSteps,
    saveSuiteSteps,
    discardSteps,
    startEditVar,
    saveVarValue,
    removeVar,
    reset,
  }
}
