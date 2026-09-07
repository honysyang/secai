/** LoginModal：API Key 登录弹窗（未授权时自动弹出）。 */

import { useEffect, useRef, useState } from 'react'
import { Modal } from '../primitives/Modal.tsx'
import { Button } from '../primitives/Button.tsx'
import { setApiKey, clearApiKey } from '../../auth/apiKeyStore.ts'
import css from './LoginModal.module.css'

export interface LoginModalProps {
  open: boolean
  onClose: () => void
  onSuccess: () => void
}

export function LoginModal({ open, onClose, onSuccess }: LoginModalProps) {
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open) {
      setKey('')
      setError('')
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [open])

  const submit = async (): Promise<void> => {
    const trimmed = key.trim()
    if (trimmed === '' || busy) return
    setBusy(true)
    setError('')
    try {
      // 用 describe 探测 key 是否有效
      const res = await fetch(`${window.location.origin}/api/describe`, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'X-API-Key': trimmed,
        },
        body: JSON.stringify({ rpcId: 'login-probe', method: 'describe', payload: {} }),
      })
      if (res.status === 401) {
        setError('Key 无效或已过期')
        return
      }
      if (!res.ok) {
        setError(`服务不可达（HTTP ${res.status}）`)
        return
      }
      const envelope = await res.json() as { ok: boolean }
      if (!envelope.ok) {
        setError('Key 无效或已过期')
        return
      }
      setApiKey(trimmed)
      onSuccess()
      onClose()
    } catch {
      setError('网络错误，请重试')
    } finally {
      setBusy(false)
    }
  }

  const handleClear = (): void => {
    clearApiKey()
    setKey('')
    setError('')
    onClose()
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="接入 SECAI-PT"
      footer={
        <>
          <Button variant="ghost" onClick={handleClear} disabled={busy}>清除并退出</Button>
          <Button variant="primary" onClick={() => void submit()} disabled={key.trim() === '' || busy}>
            {busy ? '验证中…' : '确认'}
          </Button>
        </>
      }
    >
      <div className={css.form}>
        <p className={css.desc}>请联系管理员获取 API Key</p>
        <input
          ref={inputRef}
          type="password"
          className={css.input}
          value={key}
          onChange={(e) => setKey(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') void submit() }}
          placeholder="输入 API Key"
          disabled={busy}
        />
        {error !== '' && <div className={css.error} role="alert">{error}</div>}
      </div>
    </Modal>
  )
}
