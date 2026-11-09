import { StrictMode, lazy, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// `?ui=gallery` renders the component fixtures without needing a backend.
const Gallery = lazy(() =>
  import('@/components/dev/UiGallery').then((module) => ({ default: module.UiGallery }))
)
const uiFlag = new URLSearchParams(window.location.search).get('ui')
const showGallery = uiFlag === 'gallery'

// `?ui=mock` runs the real app against fake API data (see components/dev/mock-api.ts).
const ready: Promise<unknown> =
  uiFlag === 'mock'
    ? import('@/components/dev/mock-api').then((module) => module.installMockApi())
    : Promise.resolve()

void ready.then(() => createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {showGallery ? (
      <Suspense fallback={null}>
        <Gallery />
      </Suspense>
    ) : (
      <App />
    )}
  </StrictMode>,
))
