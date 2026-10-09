import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { Collection } from '@/api/collection'

export const useAppStore = defineStore('app', () => {
  const currentCollection = ref<Collection | null>(null)

  function setCurrentCollection(c: Collection | null) {
    currentCollection.value = c
  }

  return {
    currentCollection,
    setCurrentCollection,
  }
})
