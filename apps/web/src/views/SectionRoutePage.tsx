import { useParams } from '@tanstack/react-router'

import { PlaceholderPage } from './PlaceholderPage'

export function SectionRoutePage() {
  const { section } = useParams({ from: '/$section' })
  return <PlaceholderPage section={section} />
}

