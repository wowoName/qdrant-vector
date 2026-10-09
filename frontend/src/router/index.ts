import { createRouter, createWebHistory, RouteRecordRaw } from 'vue-router'
import { isLoggedIn, getUser } from '@/utils/user'

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
    redirect: '/collections',
    children: [
      {
        path: 'collections',
        name: 'Collections',
        component: () => import('@/pages/collections/CollectionList.vue'),
        meta: { title: '知识库管理', icon: 'Collection' },
      },
      {
        path: 'collections/:id/documents',
        name: 'Documents',
        component: () => import('@/pages/documents/DocumentManager.vue'),
        meta: { title: '文档管理', icon: 'Document', hidden: true },
      },
      {
        path: 'search',
        name: 'Search',
        component: () => import('@/pages/search/VectorSearch.vue'),
        meta: { title: '向量检索', icon: 'Search' },
      },
      {
        path: 'chat',
        name: 'Chat',
        component: () => import('@/pages/chat/ChatPage.vue'),
        meta: { title: '对话验证', icon: 'ChatDot' },
      },
      {
        path: 'tasks',
        name: 'Tasks',
        component: () => import('@/pages/tasks/TaskCenter.vue'),
        meta: { title: '任务中心', icon: 'List' },
      },
      {
        path: 'admin/users',
        name: 'UserManagement',
        component: () => import('@/pages/admin/UserManagement.vue'),
        meta: { title: '用户管理', icon: 'User', requiresAdmin: true },
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
    document.title = `${title} - Vector Data Manager`
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
      next('/collections')
      return
    }
  }

  next()
})

export default router
