/**
 * MarketplaceListingDialog - Dialog for configuring marketplace listing.
 */

import { useState, useEffect } from 'react'
import { Dialog } from '@headlessui/react'
import { 
  XMarkIcon, 
  ShoppingBagIcon,
  PlusIcon,
  TrashIcon
} from '@heroicons/react/24/outline'
import { useNetworkStore, MarketplaceListing } from '../../store/networkStore'
import { getApiBase } from '../../utils/api'
import { getPaymentTokenSymbol } from '../../config/payment'

const CAPABILITY_OPTIONS = [
  'General Purpose',
  'Code Generation',
  'Code Review',
  'Data Analysis',
  'Image Generation',
  'Text Processing',
  'Research',
  'Browser Automation',
]
const KNOWN_CAPABILITY_MAP = new Map(CAPABILITY_OPTIONS.map(opt => [opt.toLowerCase(), opt]))
const EXCLUDED_SYSTEM_CAPABILITY_KEYWORDS = [
  'organizer',
  'coordinator',
  'task_decomposition',
  'worker_coordination',
  'task_routing',
  'network_delegation',
]

function isSystemCapabilityTag(name: string): boolean {
  const lowered = String(name || '').trim().toLowerCase()
  if (!lowered) return true
  return EXCLUDED_SYSTEM_CAPABILITY_KEYWORDS.some(k => lowered.includes(k))
}

function splitKnownAndCustomCapabilities(
  items: { name: string; description: string }[]
): { selectedKnown: string[]; custom: { name: string; description: string }[] } {
  const selected = new Set<string>()
  const custom: { name: string; description: string }[] = []
  const seenCustom = new Set<string>()

  for (const item of items) {
    const capName = String(item?.name || '').trim()
    if (!capName) continue
    const known = KNOWN_CAPABILITY_MAP.get(capName.toLowerCase())
    if (known) {
      selected.add(known)
      continue
    }
    const customKey = capName.toLowerCase()
    if (seenCustom.has(customKey)) continue
    seenCustom.add(customKey)
    custom.push({
      name: capName,
      description: String(item?.description || '').trim(),
    })
  }

  return { selectedKnown: Array.from(selected), custom }
}

interface Props {
  isOpen: boolean
  onClose: () => void
}

export default function MarketplaceListingDialog({ isOpen, onClose }: Props) {
  const { 
    marketplaceListing, 
    isListedOnMarketplace,
    joinMarketplace, 
    leaveMarketplace,
    updateMarketplaceListing 
  } = useNetworkStore()
  
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [selectedCapabilities, setSelectedCapabilities] = useState<string[]>([])
  const [price, setPrice] = useState('')
  const [capabilities, setCapabilities] = useState<{ name: string; description: string }[]>([])
  const [loading, setLoading] = useState(false)
  
  // Load existing listing data
  useEffect(() => {
    let cancelled = false
    const loadDefaultLocalCapabilities = async () => {
      try {
        const apiBase = getApiBase()
        const [statusRes, paymentRes] = await Promise.all([
          fetch(`${apiBase}/api/network/status`),
          fetch(`${apiBase}/api/payment/config`),
        ])
        if (!statusRes.ok) throw new Error(`HTTP ${statusRes.status}`)
        const data = await statusRes.json()
        const local = data?.local_node || {}
        const localName = String(local?.name || '').trim()
        const localDescription = String(local?.description || '').trim()

        // Default price from wallet/payment config (task_price + currency)
        let defaultPrice = 'Free'
        if (paymentRes.ok) {
          try {
            const payment = await paymentRes.json()
            const tp = payment?.task_price
            const currency = payment?.currency || getPaymentTokenSymbol()
            if (tp != null && String(tp).trim()) {
              defaultPrice = `${tp} ${currency} per task`
            }
          } catch {
            /* ignore */
          }
        }

        const normalizedCaps: { name: string; description: string }[] = []
        const seen = new Set<string>()
        const capabilities = Array.isArray(local?.capabilities) ? local.capabilities : []
        for (const item of capabilities) {
          const capName = String(item?.name || '').trim()
          if (!capName) continue
          if (isSystemCapabilityTag(capName)) continue
          const key = capName.toLowerCase()
          if (seen.has(key)) continue
          seen.add(key)
          normalizedCaps.push({
            name: capName,
            description: String(item?.description || '').trim(),
          })
        }

        const primaryCapability = String(local?.capability || '').trim()
        if (
          primaryCapability
          && !isSystemCapabilityTag(primaryCapability)
          && !seen.has(primaryCapability.toLowerCase())
        ) {
          normalizedCaps.unshift({ name: primaryCapability, description: '' })
        }

        const split = splitKnownAndCustomCapabilities(normalizedCaps)
        if (cancelled) return
        setName(localName || '')
        setDescription(localDescription || '')
        setPrice(defaultPrice)
        setSelectedCapabilities(split.selectedKnown)
        setCapabilities(split.custom)
      } catch (e) {
        console.warn('Failed to load default local capabilities:', e)
        if (cancelled) return
        setName('')
        setDescription('')
        setPrice('')
        setSelectedCapabilities([])
        setCapabilities([])
      }
    }

    if (marketplaceListing) {
      setName(marketplaceListing.name)
      setDescription(marketplaceListing.description)
      setPrice(marketplaceListing.price)
      const listingCaps: { name: string; description: string }[] = []
      if (marketplaceListing.capability?.trim()) {
        listingCaps.push({ name: marketplaceListing.capability.trim(), description: '' })
      }
      for (const cap of marketplaceListing.capabilities || []) {
        listingCaps.push({
          name: String(cap?.name || '').trim(),
          description: String(cap?.description || '').trim(),
        })
      }
      const split = splitKnownAndCustomCapabilities(listingCaps)
      setSelectedCapabilities(split.selectedKnown)
      setCapabilities(split.custom)
    } else if (isOpen) {
      void loadDefaultLocalCapabilities()
    }
    return () => {
      cancelled = true
    }
  }, [marketplaceListing, isOpen])
  
  const buildCapabilitiesPayload = () => {
    const merged: { name: string; description: string }[] = []
    const seen = new Set<string>()

    for (const name of selectedCapabilities) {
      const cleanName = name.trim()
      if (!cleanName) continue
      const key = cleanName.toLowerCase()
      if (seen.has(key)) continue
      seen.add(key)
      merged.push({ name: cleanName, description: '' })
    }

    for (const cap of capabilities) {
      const cleanName = cap.name.trim()
      if (!cleanName) continue
      if (isSystemCapabilityTag(cleanName)) continue
      const key = cleanName.toLowerCase()
      if (seen.has(key)) continue
      seen.add(key)
      merged.push({ name: cleanName, description: cap.description.trim() })
    }

    return merged
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const mergedCapabilities = buildCapabilitiesPayload()
    const primaryCapability = mergedCapabilities[0]?.name || ''
    if (!name.trim() || !primaryCapability) return
    
    setLoading(true)
    const listing: MarketplaceListing = {
      name: name.trim(),
      description: description.trim(),
      capability: primaryCapability,
      price: price.trim() || 'Free',
      capabilities: mergedCapabilities,
    }
    
    try {
      if (isListedOnMarketplace) {
        await updateMarketplaceListing(listing)
      } else {
        await joinMarketplace(listing)
      }
      onClose()
    } finally {
      setLoading(false)
    }
  }
  
  const handleLeave = async () => {
    setLoading(true)
    try {
      await leaveMarketplace()
      onClose()
    } finally {
      setLoading(false)
    }
  }
  
  const addCapability = () => {
    setCapabilities([...capabilities, { name: '', description: '' }])
  }
  
  const updateCapability = (index: number, field: 'name' | 'description', value: string) => {
    const updated = [...capabilities]
    updated[index][field] = value
    setCapabilities(updated)
  }
  
  const removeCapability = (index: number) => {
    setCapabilities(capabilities.filter((_, i) => i !== index))
  }

  const toggleCapability = (capability: string) => {
    setSelectedCapabilities(prev => {
      const exists = prev.some(c => c.toLowerCase() === capability.toLowerCase())
      if (exists) {
        return prev.filter(c => c.toLowerCase() !== capability.toLowerCase())
      }
      return [...prev, capability]
    })
  }

  const hasAnyCapability = buildCapabilitiesPayload().length > 0

  return (
    <Dialog open={isOpen} onClose={onClose} className="relative z-50">
      <div className="fixed inset-0 bg-black/60" aria-hidden="true" />
      
      <div className="fixed inset-0 flex items-center justify-center p-3 sm:p-4">
        <Dialog.Panel className="w-full max-w-2xl rounded-2xl bg-dark-surface border border-dark-border shadow-xl max-h-[90vh] overflow-hidden flex flex-col">
          {/* Header */}
          <div className="flex items-start justify-between px-5 sm:px-6 py-4 border-b border-dark-border gap-3">
            <div className="flex items-start gap-3 min-w-0">
              <div className="w-10 h-10 rounded-xl bg-primary-600/20 flex items-center justify-center">
                <ShoppingBagIcon className="w-5 h-5 text-primary-400" />
              </div>
              <div className="min-w-0">
                <Dialog.Title className="text-lg font-semibold text-white">
                  {isListedOnMarketplace ? 'Update Listing' : 'Join Agentic Node Marketplace'}
                </Dialog.Title>
                <p className="text-xs text-gray-500 break-words">
                  {isListedOnMarketplace 
                    ? 'Update your Agentic Node Marketplace listing'
                    : 'Make your node discoverable to others'
                  }
                </p>
              </div>
            </div>
            <button
              onClick={onClose}
              className="p-2 text-gray-400 hover:text-white hover:bg-dark-hover rounded-lg transition-colors"
            >
              <XMarkIcon className="w-5 h-5" />
            </button>
          </div>
          
          {/* Form */}
          <form onSubmit={handleSubmit} className="p-5 sm:p-6 space-y-4 overflow-y-auto">
            {/* Name */}
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1.5">
                Display Name *
              </label>
              <input
                type="text"
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder="My AI Agent Service"
                className="w-full px-4 py-2.5 bg-dark-bg border border-dark-border rounded-xl text-white placeholder-gray-500 focus:border-primary-500 focus:ring-1 focus:ring-primary-500 outline-none transition-colors"
                required
              />
            </div>
            
            {/* Description */}
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1.5">
                Description
              </label>
              <textarea
                value={description}
                onChange={e => setDescription(e.target.value)}
                placeholder="What does your node offer?"
                rows={3}
                className="w-full px-4 py-2.5 bg-dark-bg border border-dark-border rounded-xl text-white placeholder-gray-500 focus:border-primary-500 focus:ring-1 focus:ring-primary-500 outline-none transition-colors resize-none"
              />
            </div>
            
            {/* Capability Multi-Select */}
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1.5">
                Capability * (Multi-select)
              </label>
              <p className="text-xs text-gray-500 mb-2">Select one or more capabilities for this AN.</p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {CAPABILITY_OPTIONS.map(option => {
                  const selected = selectedCapabilities.some(c => c.toLowerCase() === option.toLowerCase())
                  return (
                    <button
                      key={option}
                      type="button"
                      onClick={() => toggleCapability(option)}
                      className={`px-3 py-2 rounded-lg text-left text-sm border transition-colors ${
                        selected
                          ? 'border-primary-500 bg-primary-500/20 text-primary-300'
                          : 'border-dark-border bg-dark-bg text-gray-300 hover:border-primary-500/50'
                      }`}
                      title={option}
                    >
                      <span className="block truncate">{option}</span>
                    </button>
                  )
                })}
              </div>
            </div>
            
            {/* Price */}
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1.5">
                Price
              </label>
              <input
                type="text"
                value={price}
                onChange={e => setPrice(e.target.value)}
                placeholder={`Free / 0.1 ${getPaymentTokenSymbol()} per task`}
                className="w-full px-4 py-2.5 bg-dark-bg border border-dark-border rounded-xl text-white placeholder-gray-500 focus:border-primary-500 focus:ring-1 focus:ring-primary-500 outline-none transition-colors"
              />
              <p className="text-xs text-gray-500 mt-1">Leave empty for free</p>
            </div>
            
            {/* Custom Capabilities */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="block text-sm font-medium text-gray-300">
                  Custom Capabilities (Optional)
                </label>
                <button
                  type="button"
                  onClick={addCapability}
                  className="flex items-center gap-1 text-xs text-primary-400 hover:text-primary-300 transition-colors"
                >
                  <PlusIcon className="w-3.5 h-3.5" />
                  Add
                </button>
              </div>
              
              {capabilities.length === 0 ? (
                <p className="text-xs text-gray-500 py-2">No custom capabilities</p>
              ) : (
                <div className="space-y-2">
                  {capabilities.map((cap, i) => (
                    <div key={i} className="flex flex-col sm:flex-row gap-2">
                      <input
                        type="text"
                        value={cap.name}
                        onChange={e => updateCapability(i, 'name', e.target.value)}
                        placeholder="Capability name"
                        className="w-full sm:flex-1 px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-sm text-white placeholder-gray-500 focus:border-primary-500 outline-none"
                      />
                      <input
                        type="text"
                        value={cap.description}
                        onChange={e => updateCapability(i, 'description', e.target.value)}
                        placeholder="Description (optional)"
                        className="w-full sm:flex-1 px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-sm text-white placeholder-gray-500 focus:border-primary-500 outline-none"
                      />
                      <button
                        type="button"
                        onClick={() => removeCapability(i)}
                        className="p-2 text-gray-500 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-colors self-end sm:self-auto"
                      >
                        <TrashIcon className="w-4 h-4" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
            
            {/* Actions */}
            <div className="flex flex-wrap gap-2 sm:gap-3 pt-4 border-t border-dark-border">
              {isListedOnMarketplace && (
                <button
                  type="button"
                  onClick={handleLeave}
                  disabled={loading}
                  className="w-full sm:w-auto px-4 py-2.5 bg-red-600/10 hover:bg-red-600/20 text-red-400 rounded-xl text-sm font-medium transition-colors border border-red-600/20 disabled:opacity-50"
                >
                  Leave Agentic Node Marketplace
                </button>
              )}
              <div className="flex-1" />
              <button
                type="button"
                onClick={onClose}
                className="px-4 py-2.5 bg-dark-hover hover:bg-dark-border text-gray-300 rounded-xl text-sm font-medium transition-colors min-w-[92px]"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={loading || !name.trim() || !hasAnyCapability}
                className="px-6 py-2.5 bg-primary-600 hover:bg-primary-500 text-white rounded-xl text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed min-w-[132px]"
              >
                {loading ? 'Saving...' : isListedOnMarketplace ? 'Update' : 'Join Agentic Node Marketplace'}
              </button>
            </div>
          </form>
        </Dialog.Panel>
      </div>
    </Dialog>
  )
}
