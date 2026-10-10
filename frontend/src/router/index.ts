import { createRouter, createWebHistory, RouteRecordRaw } from 'vue-router'
import { isLoggedIn, getUser } from '@/utils/user'
import { APP_NAME } from '@/config/app'

const routes: RouteRecordRaw[] = [
  // 认证页（无需登录）
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/pages/auth/LoginPage.vue'),
    meta: { title: '登录', public: true },
  },
  {
    path: '/register',
    name: 'Register',
    component: () => import('@/pages/auth/RegisterPage.vue'),
    meta: { title: '注册', public: true },
  },
  {
    path: '/',
    component: () => import('@/layouts/BasicLayout.vue'),
    redirect: '/chat',
    children: [
      {
        path: 'chat',
        name: 'Chat',
        component: () => import('@/pages/chat/ChatPage.vue'),
        meta: { title: '智能对话', subtitle: '基于向量知识库的检索增强问答', immersive: true },
      },
      {
        path: 'collections',
        name: 'Collections',
        component: () => import('@/pages/collections/CollectionList.vue'),
        meta: { title: '知识库管理', subtitle: '创建、配置与维护你的向量知识库' },
      },
      {
        path: 'collections/:id/documents',
        name: 'Documents',
        component: () => import('@/pages/documents/DocumentManager.vue'),
        meta: { title: '文档管理', subtitle: '上传文档并管理目录结构', hidden: true },
      },
      {
        path: 'search',
        name: 'Search',
        component: () => import('@/pages/search/VectorSearch.vue'),
        meta: { title: '向量检索', subtitle: '直接在向量空间中进行语义检索' },
      },
      {
        path: 'tasks',
        name: 'Tasks',
        component: () => import('@/pages/tasks/TaskCenter.vue'),
        meta: { title: '任务中心', subtitle: '查看文档处理与集合重建等异步任务' },
      },
      {
        path: 'admin/users',
        name: 'UserManagement',
        component: () => import('@/pages/admin/UserManagement.vue'),
        meta: {
          title: '用户管理',
          subtitle: '管理用户、租户与到期时间',
          requiresAdmin: true,
        },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/',
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to, _from, next) => {
  const title = to.meta?.title as string | undefined
  if (title) {
    document.title = `${title} - ${APP_NAME}`
  }

  // 认证守卫：未登录跳转登录页
  const isPublic = !!to.meta?.public
  if (!isPublic && !isLoggedIn()) {
    next({ path: '/login', query: to.fullPath !== '/' ? { redirect: to.fullPath } : undefined })
    return
  }
  // 已登录访问登录/注册页：跳转首页
  if (isPublic && isLoggedIn() && (to.path === '/login' || to.path === '/register')) {
    next('/')
    return
  }
  // 管理员权限守卫（super_admin 或 tenant_admin）
  if (to.meta?.requiresAdmin) {
    const role = getUser()?.role
    if (role !== 'super_admin' && role !== 'tenant_admin') {
      next('/chat')
      return
    }
  }

  next()
})

export default router
