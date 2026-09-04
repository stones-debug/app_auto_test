export function digitsOnly(value: string) {
  return value.replace(/[^0-9]/g, '')
}

export function moveToPosition<T>(items: T[], fromIndex: number, position: number) {
  if (fromIndex < 0 || fromIndex >= items.length || position < 1 || position > items.length) return null
  const targetIndex = position - 1
  if (fromIndex === targetIndex) return [...items]
  const next = [...items]
  const [item] = next.splice(fromIndex, 1)
  next.splice(targetIndex, 0, item)
  return next
}
