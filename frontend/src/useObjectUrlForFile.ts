import { useLayoutEffect, useState } from "react"

/** 隨 File 建立／釋放 blob: URL，避免手動 revoke 與 React 狀態脫勾 */
export function useObjectUrlForFile(file: File | null): string | null {
  const [url, setUrl] = useState<string | null>(null)
  useLayoutEffect(() => {
    if (!file) {
      setUrl(null)
      return
    }
    const u = URL.createObjectURL(file)
    setUrl(u)
    return () => {
      URL.revokeObjectURL(u)
    }
  }, [file])
  return url
}
