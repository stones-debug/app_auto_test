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
const fieldErrors = ref<{ username?: string; password?: string }>({})

function onUsernameBlur() {
  if (!form.value.username.trim()) {
    fieldErrors.value.username = '请输入用户名'
  } else {
    delete fieldErrors.value.username
  }
}

function onPasswordBlur() {
  if (!form.value.password) {
    fieldErrors.value.password = '请输入密码'
  } else {
    delete fieldErrors.value.password
  }
}

async function handleLogin() {
  onUsernameBlur()
  onPasswordBlur()
  if (fieldErrors.value.username || fieldErrors.value.password) return
  loading.value = true
  try {
    await auth.login(form.value.username, form.value.password, remember.value)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/dashboard'
    router.push(redirect)
  } catch {
    // 失败保留用户名，清空密码
    form.value.password = ''
    ElMessage.error('用户名或密码错误')
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
        <h2 class="welcome">欢迎回来</h2>
        <p class="subtitle">请登录你的账号以继续</p>

        <el-form label-position="top" @keyup.enter="handleLogin">
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
          <div class="row-between">
            <el-checkbox v-model="remember">记住我</el-checkbox>
          </div>
          <el-button type="primary" class="login-btn" size="large" :loading="loading" @click="handleLogin">
            登 录
          </el-button>
        </el-form>

        <div class="divider"><span>或</span></div>
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
.divider {
  display: flex;
  align-items: center;
  color: var(--text-2);
  font-size: 12px;
  margin: 24px 0 12px;
}
.divider::before,
.divider::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--border);
}
.divider span {
  padding: 0 12px;
}
.version {
  text-align: center;
  font-size: 12px;
  color: var(--text-2);
}
</style>
