import { StrictMode, lazy, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// `?ui=gallery` renders the component fixtures without needing a backend.
const Gallery = lazy(() =>
  import('@/components/dev/UiGallery').then((module) => ({ default: module.UiGallery }))
)
const showGallery = new URLSearchParams(window.location.search).get('ui') === 'gallery'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {showGallery ? (
      <Suspense fallback={null}>
        <Gallery />
      </Suspense>
    ) : (
      <App />
    )}
  </StrictMode>,
)
