/**
 * API Service - Axios instance with interceptors
 */
import axios from 'axios'
import { clearStoredAuthToken, getStoredAuthToken } from './authState'

// Create axios instance with base configuration
const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  }
})

// Request interceptor
api.interceptors.request.use(
  (config) => {
    // Add auth token if available
    const token = getStoredAuthToken()
    if (token) {
      config.headers.Authorization = `Token ${token}`
    }
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// Response interceptor
api.interceptors.response.use(
  (response) => {
    return response
  },
  (error) => {
    // Handle common errors
    if (error.response) {
      switch (error.response.status) {
        case 401:
          clearStoredAuthToken('unauthorized')
          break
        case 403:
          console.warn('Forbidden request')
          break
        case 404:
          console.warn('Resource not found')
          break
        case 500:
          console.error('Server error')
          break
      }
    } else if (error.request) {
      if (!error.config?.silentNoResponse) {
        console.error('No response received:', error.request)
      }
    } else {
      console.error('Request error:', error.message)
    }
    return Promise.reject(error)
  }
)

export const updatePonDescription = (ponId, description) =>
  api.patch(`/pons/${ponId}/`, { description })

export default api
