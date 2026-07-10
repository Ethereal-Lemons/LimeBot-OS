import { useMemo, useState } from 'react';
import { CheckCircle2, ChevronDown, ChevronUp, FileCode2, ShieldAlert, XCircle } from 'lucide-react';
import type { ChatChangeSet } from '@/lib/chat-state';
import { cn } from '@/lib/utils';

const statusTone: Record<ChatChangeSet['status'], string> = {
  planned: 'text-muted-foreground',
  awaiting_approval: 'text-amber-500',
  applied: 'text-primary',
  verified: 'text-primary',
  failed: 'text-destructive',
  blocked: 'text-destructive',
};

export function ChangeSetCard({ changeSet }: { changeSet: ChatChangeSet }) {
  const [expanded, setExpanded] = useState(false);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const files = changeSet.changed_files || [];
  const selected = useMemo(
    () => selectedFile || files[0]?.file_id || null,
    [selectedFile, files]
  );
  const latestVerification = changeSet.verification?.[changeSet.verification.length - 1];
  const isFailure = changeSet.status === 'failed' || changeSet.status === 'blocked';

  return (
    <div className="w-full max-w-2xl rounded-2xl border border-border bg-muted/60 px-4 py-3 shadow-sm">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full items-start justify-between gap-3 text-left"
      >
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-muted-foreground">
            <FileCode2 className="h-4 w-4" />
            <span>{changeSet.artifact_type === 'coding_plan' ? 'Coding plan' : 'Change set'}</span>
            <span className={cn('normal-case tracking-normal', statusTone[changeSet.status])}>
              {changeSet.status.replace('_', ' ')}
            </span>
          </div>
          <p className="mt-1 text-sm text-foreground/90 whitespace-pre-wrap">{changeSet.summary}</p>
          {changeSet.artifact_type !== 'coding_plan' && (
            <p className="mt-1 text-xs text-muted-foreground">
              {files.length} file{files.length === 1 ? '' : 's'} Â· +{changeSet.added || 0}/-{changeSet.removed || 0}
              {changeSet.truncated ? ' Â· preview truncated' : ''}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {isFailure ? <XCircle className="h-4 w-4 text-destructive" /> : changeSet.status === 'verified' ? <CheckCircle2 className="h-4 w-4 text-primary" /> : <ShieldAlert className="h-4 w-4 text-amber-500" />}
          {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        </div>
      </button>

      {expanded && (
        <div className="mt-3 border-t border-border/60 pt-3">
          {files.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {files.map((file) => (
                <button
                  key={file.file_id}
                  type="button"
                  onClick={() => setSelectedFile(file.file_id)}
                  className={cn(
                    'rounded-md border px-2 py-1 font-mono text-[11px]',
                    selected === file.file_id ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted-foreground'
                  )}
                >
                  {file.file_id} +{file.added}/-{file.removed}
                </button>
              ))}
            </div>
          )}
          {changeSet.redacted_diff && (
            <pre className="mt-3 max-h-64 overflow-auto rounded-md border border-border/60 bg-background/80 p-3 text-[11px] leading-relaxed text-muted-foreground whitespace-pre-wrap">
              {changeSet.redacted_diff}
            </pre>
          )}
          {latestVerification && (
            <div className="mt-3 rounded-md border border-border/60 bg-background/60 p-3 text-xs">
              <div className="font-semibold">{latestVerification.label}: {latestVerification.status}</div>
              {typeof latestVerification.exit_code === 'number' && <div className="mt-1 text-muted-foreground">Exit code: {latestVerification.exit_code}</div>}
              {latestVerification.diagnostic && <pre className="mt-2 whitespace-pre-wrap text-[11px] text-muted-foreground">{latestVerification.diagnostic}</pre>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
