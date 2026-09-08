import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const appProfileSource = readFileSync(resolve(process.cwd(), 'src/views/AppProfile.vue'), 'utf8')
const devicePickerSource = readFileSync(resolve(process.cwd(), 'src/components/DevicePicker.vue'), 'utf8')

describe('APP 档案运行全部套件', () => {
  it('运行入口使用 profile_all，不分页收集 workspace 套件 ID', () => {
    const runAll = appProfileSource.match(/async function runAllSuites\(\)[\s\S]*?(?=\nasync function updateRevision)/)?.[0] ?? ''

    expect(runAll).toContain("targetScope: 'profile_all'")
    expect(runAll).toContain('suiteIds: []')
    expect(runAll).not.toContain('workspace(')
  })

  it('档案视图已传入发布版本时，DevicePicker 不重复加载 releases', () => {
    const readonlyPath = devicePickerSource.match(
      /if \(profileReadonly\.value && profileId\.value != null\) \{[\s\S]*?\n    \}/,
    )

    expect(readonlyPath).toBeNull()
    expect(devicePickerSource).toContain('releaseId.value = options.profile.app_release_id')
    expect(devicePickerSource).toContain('releases.value = [profileReleaseOption(options.profile)]')
    expect(devicePickerSource).toContain("if (!options.profile && target.kind !== 'retry'")
  })

  it('预检 token 失效时提示重新预检并刷新预检结果', () => {
    const branch = devicePickerSource.match(
      /if \(apiErrorCode\(error\) === 'EXECUTION_PREPARE_INVALID'\) \{[\s\S]*?\n    \}/,
    )?.[0] ?? ''
    expect(branch).toContain('预检已失效，已重新预检，请再次运行')
    expect(branch).toContain('await doPreview()')
  })
})
