import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import router from './router'
import './style.css'

// 命令式 API（ElMessage/ElMessageBox）不经 unplugin 模板扫描，其样式不会被按需注入，
// 需全局引入；否则消息框/输入框样式丢失、弹窗错位到左上角。
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/es/components/input/style/css'

const app = createApp(App)

app.use(createPinia())
app.use(router)

app.mount('#app')
