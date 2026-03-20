export type Page = 'files' | 'text' | 'header'

interface Props {
  active: Page
  onChange: (p: Page) => void
}

const TABS: { id: Page; label: string; icon: string }[] = [
  { id: 'files',  label: '文件翻译', icon: '📁' },
  { id: 'text',   label: '文本翻译', icon: '✏️' },
  { id: 'header', label: '注释生成', icon: '⚙️' },
]

export default function NavTabs({ active, onChange }: Props) {
  return (
    <div className="nav-tabs">
      {TABS.map(t => (
        <button
          key={t.id}
          className={`nav-tab ${active === t.id ? 'active' : ''}`}
          onClick={() => onChange(t.id)}
        >
          <span className="nav-tab-icon">{t.icon}</span>
          <span>{t.label}</span>
        </button>
      ))}
    </div>
  )
}
