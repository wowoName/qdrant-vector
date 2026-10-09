import { createRouter, createWebHistory, RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
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
    ],
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
  next()
})

export default router
