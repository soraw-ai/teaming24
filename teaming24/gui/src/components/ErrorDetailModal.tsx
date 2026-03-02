import { Dialog } from '@headlessui/react'
import { ExclamationTriangleIcon, XMarkIcon } from '@heroicons/react/24/outline'
import { useUiStore } from '../store/uiStore'

export default function ErrorDetailModal() {
  const { activeError, clearError } = useUiStore()

  return (
    <Dialog open={Boolean(activeError)} onClose={clearError} className="relative z-[999999]">
      <div className="fixed inset-0 bg-black/70" aria-hidden="true" />
      <div className="fixed inset-0 p-3 sm:p-4 flex items-center justify-center">
        <Dialog.Panel className="w-full max-w-2xl max-h-[88vh] overflow-hidden rounded-2xl bg-dark-surface border border-dark-border shadow-xl flex flex-col">
          <div className="px-5 sm:px-6 py-4 border-b border-dark-border flex items-start justify-between gap-3">
            <div className="min-w-0 flex items-start gap-2">
              <ExclamationTriangleIcon className="w-5 h-5 text-red-400 mt-0.5 shrink-0" />
              <div className="min-w-0">
                <Dialog.Title className="text-base font-semibold text-white truncate">
                  {activeError?.title || 'Error'}
                </Dialog.Title>
                <p className="text-xs text-gray-500 mt-1 break-words">
                  {activeError?.message}
                </p>
                {activeError?.source && (
                  <p className="text-[11px] text-gray-600 mt-1">
                    source: {activeError.source}
                  </p>
                )}
              </div>
            </div>
            <button
              onClick={clearError}
              className="p-2 rounded-lg text-gray-400 hover:text-white hover:bg-dark-hover transition-colors"
              aria-label="Close error details"
            >
              <XMarkIcon className="w-5 h-5" />
            </button>
          </div>

          <div className="p-5 sm:p-6 overflow-y-auto">
            <div className="rounded-lg border border-dark-border bg-dark-bg p-3">
              <p className="text-xs text-gray-500 mb-2">Details</p>
              <pre className="text-xs text-red-200 whitespace-pre-wrap break-words overflow-x-auto">
                {activeError?.details || 'No additional details.'}
              </pre>
            </div>
          </div>
        </Dialog.Panel>
      </div>
    </Dialog>
  )
}
