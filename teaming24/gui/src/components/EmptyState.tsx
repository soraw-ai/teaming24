import { 
  ChatBubbleLeftRightIcon, 
  CommandLineIcon, 
  RocketLaunchIcon,
  CircleStackIcon,
} from '@heroicons/react/24/outline'

const suggestions = [
  {
    icon: CommandLineIcon,
    title: 'Audit This Repo',
    description: 'Run a deep code review for bug risks, data consistency, and missing tests.',
    prompt: 'Run a full code review on this repository. Focus on bug risks, data consistency issues, security concerns, and missing tests. Return prioritized findings with concrete fix suggestions.',
  },
  {
    icon: RocketLaunchIcon,
    title: 'Design a New Feature',
    description: 'Draft architecture, API changes, and a phased implementation plan.',
    prompt: 'Design a new feature for this project: include architecture changes, API/schema updates, migration strategy, rollout plan, and test plan.',
  },
  {
    icon: CircleStackIcon,
    title: 'Monetize Data/Model/Compute',
    description: 'Create a concrete monetization strategy for assets on AgentaNet.',
    prompt: 'Create a practical monetization plan for data, ML models, multi-agent workflows, and compute on AgentaNet, including pricing, payment flow (x402), and trust/risk controls.',
  },
]

export default function EmptyState({
  onSuggestionSelect,
}: {
  onSuggestionSelect?: (prompt: string) => void
}) {
  return (
    <div className="flex flex-col items-center justify-center h-full px-4 py-12">
      {/* Logo */}
      <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center mb-6 shadow-lg shadow-primary-500/20">
        <ChatBubbleLeftRightIcon className="w-10 h-10 text-white" />
      </div>

      {/* Title */}
      <h2 className="text-2xl font-bold text-white mb-2">Welcome to Teaming24</h2>
      <p className="text-gray-400 text-center max-w-md mb-8">
        Your intelligent multi-agent collaboration platform. Start a conversation to explore the possibilities.
      </p>

      {/* Suggestions */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 max-w-3xl w-full">
        {suggestions.map((suggestion) => (
          <button
            key={suggestion.title}
            onClick={() => onSuggestionSelect?.(suggestion.prompt)}
            className="flex flex-col items-start gap-2 p-4 bg-dark-surface border border-dark-border 
                       rounded-xl hover:border-primary-500/50 hover:bg-dark-hover transition-all text-left group"
          >
            <suggestion.icon className="w-6 h-6 text-primary-500 group-hover:text-primary-400 transition-colors" />
            <div>
              <h3 className="font-medium text-gray-200 group-hover:text-white transition-colors">
                {suggestion.title}
              </h3>
              <p className="text-sm text-gray-500">{suggestion.description}</p>
            </div>
          </button>
        ))}
      </div>

      {/* Keyboard shortcuts hint */}
      <div className="mt-12 flex items-center gap-6 text-sm text-gray-500">
        <div className="flex items-center gap-2">
          <kbd className="px-2 py-1 bg-dark-surface border border-dark-border rounded text-xs">Enter</kbd>
          <span>to send</span>
        </div>
        <div className="flex items-center gap-2">
          <kbd className="px-2 py-1 bg-dark-surface border border-dark-border rounded text-xs">Shift + Enter</kbd>
          <span>new line</span>
        </div>
      </div>
    </div>
  )
}
