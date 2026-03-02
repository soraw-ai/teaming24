/**
 * Centralized API base URL accessor.
 *
 * All frontend code should import `getApiBase` instead of inlining
 * `import.meta.env.VITE_API_BASE_URL || ''` everywhere.
 */

/** Return the backend API base URL (empty string when using Vite proxy). */
export function getApiBase(): string {
  return import.meta.env.VITE_API_BASE_URL || ''
}

/**
 * Alias for getApiBase — same behavior, kept for backward compatibility with stores.
 * @deprecated Prefer getApiBase()
 */
export const getApiBaseAbsolute = getApiBase
