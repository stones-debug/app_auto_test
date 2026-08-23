<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const form = ref({ username: '', password: '' })
const remember = ref(true)
const loading = ref(false)
const mode = ref<'login' | 'register'>('login')
const fieldErrors = ref<{ username?: string; password?: string }>({})
const registerError = ref('')

function switchMode(m: 'login' | 'register') {
  if (mode.value === m) return
  mode.value = m
  registerError.value = ''
  fieldErrors.value = {}
  form.value.password = ''
}

function onUsernameBlur() {
  const name = form.value.username.trim()
  if (!name) {
    fieldErrors.value.username = '请输入用户名'
  } else if (mode.value === 'register' && name.length < 3) {
    fieldErrors.value.username = '用户名至少 3 个字符'
  } else {
    delete fieldErrors.value.username
  }
}

function onPasswordBlur() {
  const pwd = form.value.password
  if (!pwd) {
    fieldErrors.value.password = '请输入密码'
  } else if (mode.value === 'register' && pwd.length < 6) {
    fieldErrors.value.password = '密码至少 6 位'
  } else {
    delete fieldErrors.value.password
  }
}

function errorDetail(e: unknown) {
  const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return typeof d === 'string' ? d : undefined
}

async function handleSubmit() {
  onUsernameBlur()
  onPasswordBlur()
  if (fieldErrors.value.username || fieldErrors.value.password) return
  loading.value = true
  registerError.value = ''
  try {
    if (mode.value === 'register') {
      await auth.register(form.value.username.trim(), form.value.password)
    } else {
      await auth.login(form.value.username.trim(), form.value.password, remember.value)
    }
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/dashboard'
    router.push(redirect)
  } catch (e) {
    if (mode.value === 'register') {
      registerError.value = errorDetail(e) ?? '注册失败，请稍后重试'
    } else {
      // 失败保留用户名，清空密码
      form.value.password = ''
      ElMessage.error('用户名或密码错误')
    }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="brand">
      <div class="deco deco-1"></div>
      <div class="deco deco-2"></div>
      <div class="brand-inner">
        <img src="/favicon.svg" alt="APP 自动化测试平台" class="brand-logo" />
        <div class="brand-title">APP 自动化测试平台</div>
        <div class="brand-desc">一站式移动端自动化测试解决方案</div>
        <ul class="highlights">
          <li>双平台支持 · Android / iOS</li>
          <li>智能执行 · 设备池调度与实时监控</li>
          <li>实时报告 · 一键导出离线 HTML</li>
        </ul>
      </div>
    </div>

    <div class="form-side">
      <div class="login-card">
        <h2 class="welcome">{{ mode === 'login' ? '欢迎回来' : '创建账号' }}</h2>
        <p class="subtitle">{{ mode === 'login' ? '请登录你的账号以继续' : '填写用户名和密码即可注册' }}</p>

        <el-form label-position="top" @keyup.enter="handleSubmit">
          <el-form-item label="用户名" :error="fieldErrors.username">
            <el-input v-model="form.username" placeholder="请输入用户名" size="large" @blur="onUsernameBlur" />
          </el-form-item>
          <el-form-item label="密码" :error="fieldErrors.password">
            <el-input
              v-model="form.password"
              type="password"
              placeholder="请输入密码"
              show-password
              size="large"
              @blur="onPasswordBlur"
            />
          </el-form-item>
          <div v-if="mode === 'login'" class="row-between">
            <el-checkbox v-model="remember">记住我</el-checkbox>
          </div>
          <el-alert
            v-if="registerError"
            class="reg-error"
            type="error"
            :closable="false"
            show-icon
            :title="registerError"
          />
          <el-button type="primary" class="login-btn" size="large" :loading="loading" @click="handleSubmit">
            {{ mode === 'login' ? '登 录' : '注 册' }}
          </el-button>
        </el-form>

        <div class="switch-mode">
          <template v-if="mode === 'login'">
            没有账号？<a @click="switchMode('register')">立即注册</a>
          </template>
          <template v-else>
            已有账号？<a @click="switchMode('login')">去登录</a>
          </template>
        </div>
        <div class="version">APP 自动化测试平台 v1.0</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  height: 100vh;
  display: flex;
  background: var(--bg);
}

/* 左侧品牌区 55% */
.brand {
  position: relative;
  width: 55%;
  background: #f0f6ff;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}
.deco {
  position: absolute;
  border-radius: 50%;
  background: rgba(79, 70, 229, 0.08);
}
.deco-1 {
  width: 360px;
  height: 360px;
  top: -120px;
  right: -80px;
}
.deco-2 {
  width: 260px;
  height: 260px;
  bottom: -90px;
  left: -60px;
  background: rgba(16, 185, 129, 0.07);
}
.brand-inner {
  position: relative;
  max-width: 420px;
  padding: 0 24px;
}
.brand-logo {
  width: 72px;
  height: 72px;
  border-radius: 16px;
  margin-bottom: 20px;
  box-shadow: 0 10px 24px rgba(79, 70, 229, 0.25);
}
.brand-title {
  font-size: 34px;
  font-weight: 700;
  color: var(--text);
  margin-bottom: 12px;
}
.brand-desc {
  font-size: 15px;
  color: var(--text-2);
  margin-bottom: 36px;
}
.highlights {
  list-style: none;
}
.highlights li {
  font-size: 14px;
  color: var(--text);
  padding: 10px 0 10px 18px;
  border-left: 3px solid var(--primary);
  margin-bottom: 12px;
  background: #fff;
  border-radius: 0 8px 8px 0;
}

/* 右侧登录区 45% */
.form-side {
  width: 45%;
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
}
.login-card {
  width: 380px;
}
.welcome {
  font-size: 24px;
  font-weight: 700;
  color: var(--text);
  margin-bottom: 6px;
}
.subtitle {
  font-size: 14px;
  color: var(--text-2);
  margin-bottom: 28px;
}
.row-between {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}
.login-btn {
  width: 100%;
  background: linear-gradient(135deg, #4f46e5, #4338ca);
  border: none;
  transition: box-shadow 0.2s;
}
.login-btn:hover {
  box-shadow: 0 6px 16px rgba(79, 70, 229, 0.35);
}
.reg-error {
  margin-bottom: 16px;
}
.switch-mode {
  text-align: center;
  margin-top: 18px;
  font-size: 13px;
  color: var(--text-2);
}
.switch-mode a {
  color: var(--primary);
  cursor: pointer;
  font-weight: 500;
}
.switch-mode a:hover {
  text-decoration: underline;
}
.version {
  text-align: center;
  font-size: 12px;
  color: var(--text-2);
}
</style>
