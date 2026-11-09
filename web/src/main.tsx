import { StrictMode, lazy, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// `?ui=gallery` renders the chat component fixtures, `?ui=ops` the operations
// pages and `?ui=pages` the data pages (Memory, Channels, Cron, Browser sessions,
// against an in-memory mock backend). None of them needs a real backend.
const Gallery = lazy(() =>
  import('@/components/dev/UiGallery').then((module) => ({ default: module.UiGallery }))
)
const OpsGallery = lazy(() =>
  import('@/components/dev/OpsGallery').then((module) => ({ default: module.OpsGallery }))
)
const PagesGallery = lazy(() =>
  import('@/components/dev/PagesGallery').then((module) => ({ default: module.PagesGallery }))
)
const uiFlag = new URLSearchParams(window.location.search).get('ui')
const showGallery = uiFlag === 'gallery'
const showOpsGallery = uiFlag === 'ops'
const showPages = uiFlag === 'pages'

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
    ) : showOpsGallery ? (
      <Suspense fallback={null}>
        <OpsGallery />
      </Suspense>
    ) : showPages ? (
      <Suspense fallback={null}>
        <PagesGallery />
      </Suspense>
    ) : (
      <App />
    )}
  </StrictMode>,
))
