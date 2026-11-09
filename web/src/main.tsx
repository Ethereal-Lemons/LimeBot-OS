import { StrictMode, lazy, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// `?ui=gallery` renders the chat component fixtures and `?ui=ops` the operations
// pages against canned data, both without needing a backend.
const Gallery = lazy(() =>
  import('@/components/dev/UiGallery').then((module) => ({ default: module.UiGallery }))
)
const OpsGallery = lazy(() =>
  import('@/components/dev/OpsGallery').then((module) => ({ default: module.OpsGallery }))
)
const uiMode = new URLSearchParams(window.location.search).get('ui')
const showGallery = uiMode === 'gallery'
const showOpsGallery = uiMode === 'ops'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {showGallery ? (
      <Suspense fallback={null}>
        <Gallery />
      </Suspense>
    ) : showOpsGallery ? (
      <Suspense fallback={null}>
        <OpsGallery />
      </Suspense>
    ) : (
      <App />
    )}
  </StrictMode>,
)
