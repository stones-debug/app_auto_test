import { describe, expect, it } from 'vitest'

import { toSuiteStepPayload } from '@/utils/suiteSteps'

describe('套件前后置步骤提交', () => {
  it('只提交动作字段，不携带通用编辑器的断言元数据', () => {
    const payload = toSuiteStepPayload([
      {
        key: 'step-1',
        order: 1,
        phase: 'setup',
        action: 'launch_app',
        params: { package: '${package_name}', activity: null, no_reset: true },
        element_id: null,
        description: '',
        continue_on_failure: false,
        assertions: [],
      },
    ])

    expect(payload).toEqual([
      {
        key: 'step-1',
        order: 1,
        phase: 'setup',
        action: 'launch_app',
        params: { package: '${package_name}', activity: null, no_reset: true },
        element_id: null,
        description: '',
        continue_on_failure: false,
      },
    ])
  })
})
