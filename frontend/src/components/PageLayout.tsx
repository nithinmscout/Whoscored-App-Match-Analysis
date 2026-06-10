import type { ReactNode } from 'react'

interface Props {
  title: string
  caption?: string
  children: ReactNode
}

export default function PageLayout({ title, caption, children }: Props) {
  return (
    <div>
      <h1 style={{ fontSize: 22, margin: '0 0 4px' }}>{title}</h1>
      {caption && <p style={{ color: '#888', fontSize: 13, margin: '0 0 20px' }}>{caption}</p>}
      {children}
    </div>
  )
}
