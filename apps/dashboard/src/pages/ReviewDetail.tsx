import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { get, post } from '../lib/api'
import type { LineageObservation, ReviewTaskDetail } from '../lib/types'
import { PageHeader } from '../components/PageHeader'
import { Badge, StatusBadge } from '../components/Badge'
import { ConfidenceBar } from '../components/ConfidenceBar'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { JsonViewer } from '../components/JsonViewer'
import { ErrorNote, KV, errorMessage } from '../components/misc'
import { useAuth } from '../lib/auth'
import { fmtAge, fmtCredits } from '../lib/format'

interface ActionBody {
  action: string
  selected_observation_id?: string
  corrected_value?: string
  note?: string
}

export function ReviewDetailPage() {
  const { taskId = '' } = useParams()
  const { hasRole } = useAuth()
  const canReview = hasRole('reviewer')
  const qc = useQueryClient()
  const [note, setNote] = useState('')
  const [corrected, setCorrected] = useState('')
  const [confirmReverse, setConfirmReverse] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  const detail = useQuery({
    queryKey: ['review', 'task', taskId],
    queryFn: () => get<ReviewTaskDetail>(`/v1/review/tasks/${taskId}`),
  })

  const act = useMutation({
    mutationFn: (body: ActionBody) =>
      post(`/v1/review/tasks/${taskId}/actions`, { ...body, note: body.note || note || undefined }),
    onSuccess: () => {
      setActionError(null)
      setNote('')
      setCorrected('')
      void qc.invalidateQueries({ queryKey: ['review'] })
    },
    onError: (e) => setActionError(errorMessage(e)),
  })
  const reverse = useMutation({
    mutationFn: () => post(`/v1/review/tasks/${taskId}/reverse`),
    onSuccess: () => {
      setActionError(null)
      void qc.invalidateQueries({ queryKey: ['review'] })
    },
    onError: (e) => setActionError(errorMessage(e)),
  })

  const d = detail.data
  const task = d?.task
  const isResolved = task ? ['accepted', 'overridden', 'rejected'].includes(task.status) : false
  const fieldObs: LineageObservation[] = (d?.observations ?? []).filter(
    (o) => !task?.field_name || (o.field_name ?? task.field_name) === task.field_name,
  )
  const reconciliation = d?.lineage?.reconciliations?.at(-1)

  return (
    <>
      <PageHeader
        title={task?.field_name ? `Review — ${task.field_name}` : 'Review — record'}
        subtitle={task ? `${task.entity_type} · task ${task.id}` : ''}
        actions={task && <StatusBadge status={task.status} />}
      />
      {detail.isError && <ErrorNote error={detail.error} />}
      {actionError && <div className="error-note" role="alert">{actionError}</div>}

      {task && (
        <section className="panel">
          <KV
            pairs={[
              ['Reason', task.reason],
              ['Confidence', task.confidence != null ? <ConfidenceBar key="c" value={task.confidence} /> : '—'],
              ['Suggested value', task.suggested_value ?? '—'],
              ['Opened', fmtAge(task.created_at)],
            ]}
          />
        </section>
      )}

      {d?.entity && (
        <section className="panel">
          <h3>Original record</h3>
          <KV pairs={Object.entries(d.entity).map(([k, v]) => [k.replace(/_/g, ' '), String(v ?? '—')])} />
        </section>
      )}

      {reconciliation && (
        <section className="panel">
          <h3>Why this needs review</h3>
          <blockquote className="reasoning">{reconciliation.reasoning}</blockquote>
          <JsonViewer data={reconciliation.factors} label="reconciliation factors" />
        </section>
      )}

      <h2 className="section-title">Provider observations</h2>
      <div className="obs-grid">
        {fieldObs.map((o) => (
          <div key={o.id} className={`obs-card ${o.id === task?.suggested_observation_id ? 'selected' : ''}`}>
            <div className="obs-card-head">
              <Badge tone="accent">{o.provider}</Badge>
              {o.id === task?.suggested_observation_id && <Badge tone="ok">suggested</Badge>}
              {o.is_rejected && <Badge tone="danger">rejected</Badge>}
            </div>
            <div className="obs-value">{o.normalized_value ?? o.raw_value ?? '—'}</div>
            <KV
              pairs={[
                ['Cost', fmtCredits(o.cost_credits)],
                ['Source age', o.source_timestamp ? fmtAge(o.source_timestamp) : '—'],
                ['Provider conf.', o.provider_confidence?.toFixed(2) ?? '—'],
                ['Staleness', o.staleness_state ?? 'unknown'],
              ]}
            />
            {canReview && !isResolved && (
              <button
                type="button"
                className="btn small"
                disabled={act.isPending}
                onClick={() => act.mutate({ action: 'select_observation', selected_observation_id: o.id })}
              >
                Use this value
              </button>
            )}
          </div>
        ))}
        {fieldObs.length === 0 && <span className="faint">No observations recorded for this field.</span>}
      </div>

      {canReview && (
        <section className="panel action-panel">
          <h3>Decide</h3>
          <label className="field-label" htmlFor="review-note">Review note (recorded in the audit log)</label>
          <textarea
            id="review-note"
            className="input"
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Why you decided this way…"
          />
          <div className="btn-row">
            <button type="button" className="btn primary" disabled={act.isPending || isResolved}
              onClick={() => act.mutate({ action: 'accept_suggested' })}>
              Accept suggested
            </button>
            <button type="button" className="btn danger" disabled={act.isPending || isResolved}
              onClick={() => act.mutate({ action: 'reject' })}>
              Reject record
            </button>
            <button type="button" className="btn" disabled={act.isPending || isResolved}
              onClick={() => act.mutate({ action: 'defer' })}>
              Defer
            </button>
            <button type="button" className="btn" disabled={act.isPending || !note}
              onClick={() => act.mutate({ action: 'add_note' })}>
              Add note only
            </button>
            {isResolved && (
              <button type="button" className="btn warn" onClick={() => setConfirmReverse(true)}>
                Reverse decision
              </button>
            )}
          </div>
          <div className="btn-row">
            <label className="field-label" htmlFor="corrected-value">Or enter a corrected value</label>
            <input
              id="corrected-value"
              className="input"
              value={corrected}
              onChange={(e) => setCorrected(e.target.value)}
              placeholder="Corrected value"
            />
            <button type="button" className="btn" disabled={act.isPending || isResolved || !corrected}
              onClick={() => act.mutate({ action: 'correct_value', corrected_value: corrected })}>
              Apply correction
            </button>
          </div>
        </section>
      )}

      <h2 className="section-title">Decision history</h2>
      <div className="panel">
        {(d?.decisions ?? []).length === 0 && <span className="faint">No decisions yet.</span>}
        {(d?.decisions ?? []).map((dec) => (
          <div key={dec.id} className="stage-item">
            <Badge tone={dec.action === 'reverse' ? 'warn' : 'accent'}>{dec.action}</Badge>{' '}
            <span className="faint">
              {fmtAge(dec.created_at)}
              {dec.corrected_value ? ` · corrected to “${dec.corrected_value}”` : ''}
              {dec.note ? ` · “${dec.note}”` : ''}
              {dec.reverses_decision_id ? ' · reverses a prior decision' : ''}
            </span>
            <JsonViewer data={dec.previous_state} label="state before this action" />
          </div>
        ))}
      </div>

      <ConfirmDialog
        open={confirmReverse}
        title="Reverse this approval?"
        message="The canonical value returns to its prior state. Nothing is deleted — the reversal is appended to the audit history."
        confirmLabel="Reverse"
        danger
        busy={reverse.isPending}
        onConfirm={() => { setConfirmReverse(false); reverse.mutate() }}
        onCancel={() => setConfirmReverse(false)}
      />
    </>
  )
}
