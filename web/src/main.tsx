import { StrictMode, lazy, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// `?ui=gallery` renders the chat tool fixtures and `?ui=pages` the data pages
// (against an in-memory mock backend), neither needs a real backend.
const Gallery = lazy(() =>
  import('@/components/dev/UiGallery').then((module) => ({ default: module.UiGallery }))
)
const PagesGallery = lazy(() =>
  import('@/components/dev/PagesGallery').then((module) => ({ default: module.PagesGallery }))
)
const uiMode = new URLSearchParams(window.location.search).get('ui')
const showGallery = uiMode === 'gallery'
const showPages = uiMode === 'pages'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {showGallery ? (
      <Suspense fallback={null}>
        <Gallery />
      </Suspense>
    ) : showPages ? (
      <Suspense fallback={null}>
        <PagesGallery />
      </Suspense>
    ) : (
      <App />
    )}
  </StrictMode>,
)
