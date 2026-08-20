<script setup lang="ts">
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
</script>

<template>
  <el-container class="layout">
    <el-aside width="220px">
      <div class="logo">APP 自动化测试平台</div>
      <el-menu router :default-active="$route.path" class="menu">
        <el-menu-item index="/projects">项目管理</el-menu-item>
        <el-menu-item index="/executions">执行记录</el-menu-item>
        <el-menu-item index="/devices">设备管理</el-menu-item>
        <el-menu-item index="/reports">报告管理</el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">
        <span class="page-title">{{ $route.meta.title ?? '' }}</span>
        <el-dropdown @command="auth.logout">
          <span class="user">{{ auth.user?.username ?? '未登录' }}</span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="logout">退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </el-header>
      <el-main>
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.layout {
  height: 100vh;
}
.logo {
  height: 60px;
  line-height: 60px;
  text-align: center;
  font-weight: 600;
  color: #fff;
  background: #001529;
}
.menu {
  border-right: none;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #eee;
}
.user {
  cursor: pointer;
  color: #409eff;
}
</style>