import { useState, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { 
  BookOpenIcon, 
  ChevronRightIcon,
  HomeIcon,
  ArrowLeftIcon
} from '@heroicons/react/24/outline'
import clsx from 'clsx'
import { getApiBase } from '../utils/api'

interface DocItem {
  id: string
  title: string
  path: string
}


const DOCS: DocItem[] = [
  { id: 'readme', title: 'Overview', path: 'README.md' },
  { id: 'getting-started', title: 'Getting Started', path: 'getting-started.md' },
  { id: 'configuration', title: 'Configuration', path: 'configuration.md' },
  { id: 'architecture', title: 'Architecture', path: 'architecture.md' },
  { id: 'x402-payments', title: 'x402 Payments', path: 'x402-payments.md' },
  { id: 'api', title: 'API Reference', path: 'api.md' },
  { id: 'cli', title: 'CLI Reference', path: 'cli.md' },
]

export default function DocsView() {
  const [activeDoc, setActiveDoc] = useState<string>('readme')
  const [content, setContent] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const loadDoc = async () => {
      setLoading(true)
      setError(null)
      
      const doc = DOCS.find(d => d.id === activeDoc)
      if (!doc) return

      try {
        const response = await fetch(`${getApiBase()}/api/docs/${doc.path}`)
        if (!response.ok) {
          throw new Error(`Failed to load ${doc.path}`)
        }
        const text = await response.text()
        setContent(text)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load documentation')
      } finally {
        setLoading(false)
      }
    }

    loadDoc()
  }, [activeDoc])

  const currentDoc = DOCS.find(d => d.id === activeDoc)
  const currentIndex = DOCS.findIndex(d => d.id === activeDoc)
  const prevDoc = currentIndex > 0 ? DOCS[currentIndex - 1] : null
  const nextDoc = currentIndex < DOCS.length - 1 ? DOCS[currentIndex + 1] : null

  return (
    <div className="flex h-full">
      {/* Sidebar Navigation */}
      <aside className="w-64 border-r border-dark-border bg-dark-surface flex flex-col">
        <div className="p-4 border-b border-dark-border">
          <div className="flex items-center gap-2">
            <BookOpenIcon className="w-5 h-5 text-primary-400" />
            <span className="font-semibold text-white">Documentation</span>
          </div>
        </div>
        
        <nav className="flex-1 p-3 space-y-1 overflow-y-auto">
          {DOCS.map((doc) => (
            <button
              key={doc.id}
              onClick={() => setActiveDoc(doc.id)}
              className={clsx(
                'w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors text-left',
                activeDoc === doc.id
                  ? 'bg-primary-500/20 text-primary-400'
                  : 'text-gray-400 hover:bg-dark-hover hover:text-gray-200'
              )}
            >
              <ChevronRightIcon className={clsx(
                'w-4 h-4 transition-transform',
                activeDoc === doc.id && 'rotate-90'
              )} />
              {doc.title}
            </button>
          ))}
        </nav>

        <div className="p-3 border-t border-dark-border">
          <a 
            href="https://github.com/teaming24/teaming24/docs" 
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-500 hover:text-gray-300 transition-colors"
          >
            <HomeIcon className="w-4 h-4" />
            View on GitHub
          </a>
        </div>
      </aside>

      {/* Content Area */}
      <main className="flex-1 flex flex-col min-w-0 bg-dark-bg">
        {/* Breadcrumb */}
        <div className="flex items-center gap-2 px-6 py-2 text-sm text-gray-400 border-b border-dark-border bg-dark-bg/50">
          <span>Docs</span>
          <ChevronRightIcon className="w-4 h-4" />
          <span className="text-white">{currentDoc?.title}</span>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto">
          <div className="max-w-4xl mx-auto px-6 py-8">
            {loading ? (
              <div className="flex items-center justify-center py-20">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-500"></div>
              </div>
            ) : error ? (
              <div className="text-center py-20">
                <p className="text-red-400 mb-4">{error}</p>
                <button 
                  onClick={() => setActiveDoc('readme')}
                  className="text-primary-400 hover:underline"
                >
                  Go to Overview
                </button>
              </div>
            ) : (
              <article className="prose prose-invert prose-headings:text-white prose-p:text-gray-300 prose-a:text-primary-400 prose-code:text-primary-300 prose-code:bg-dark-surface prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-pre:bg-dark-surface prose-pre:border prose-pre:border-dark-border prose-strong:text-white prose-table:text-gray-300 prose-th:text-gray-200 prose-th:border-dark-border prose-td:border-dark-border max-w-none">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
              </article>
            )}

            {/* Navigation */}
            {!loading && !error && (
              <div className="flex items-center justify-between mt-12 pt-6 border-t border-dark-border">
                {prevDoc ? (
                  <button
                    onClick={() => setActiveDoc(prevDoc.id)}
                    className="flex items-center gap-2 text-gray-400 hover:text-white transition-colors"
                  >
                    <ArrowLeftIcon className="w-4 h-4" />
                    <span className="text-sm">{prevDoc.title}</span>
                  </button>
                ) : <div />}
                
                {nextDoc && (
                  <button
                    onClick={() => setActiveDoc(nextDoc.id)}
                    className="flex items-center gap-2 text-gray-400 hover:text-white transition-colors"
                  >
                    <span className="text-sm">{nextDoc.title}</span>
                    <ChevronRightIcon className="w-4 h-4" />
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  )
}
