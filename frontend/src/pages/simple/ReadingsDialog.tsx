// AI readings by hand (设置里选了“手动（网页聊天）”): the task waits right after it is added until
// the prompt has been sent to any AI web chat and its reply pasted back here.  The reply is checked
// like a pasted reply in the detailed mode; the lines that pass are applied, the rest keep the rule
// readings.  Skipping goes on with the rule readings.

import { Check, ClipboardCopy, Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { copyText } from '@/lib/format';
import type { PipelineTask } from '@/lib/types';
import { run, toast } from '@/store/app';
import { usePageDraft } from '@/store/drafts';
import { readingsPrompt, submitReadings } from '@/store/simple';
import { Button, Callout, Dialog, Textarea } from '@/components/ui';

export function ReadingsDialog({ task, onClose }: { task: PipelineTask; onClose: () => void }) {
  const [prompt, setPrompt] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  // the pasted reply survives closing the dialog ("稍后") and leaving the page
  const [reply, setReply] = usePageDraft(`simple.readings.${task.id}`, '');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<'submit' | 'skip' | null>(null);

  useEffect(() => {
    let off = false;
    readingsPrompt(task.id).then((r) => { if (!off) setPrompt(r.prompt); })
      .catch((e: Error) => { if (!off) setLoadError(e.message); });
    return () => { off = true; };
  }, [task.id]);

  const copy = async () => {
    if (!prompt) return;
    if (await copyText(prompt)) {
      setCopied(true);
      toast('ok', '已复制提示词', '粘贴到 AI 聊天网页发送，再把完整回复粘贴回来');
    } else {
      toast('warn', '无法自动复制', '请在下面的文本框里全选后手动复制');
    }
  };

  const send = (body: { text?: string; skip?: boolean }) => run(async () => {
    setBusy(body.skip ? 'skip' : 'submit');
    setError(null);
    try {
      await submitReadings(task.id, body);
      setReply('');
      toast('ok', body.skip ? '已跳过 AI 注音，任务继续' : '已采用 AI 注音，任务继续', '接下来全部自动完成');
      onClose();
    } catch (e) {
      setError((e as Error).message);  // shown in the dialog; the task keeps waiting
    } finally {
      setBusy(null);
    }
  });

  return (
    <Dialog
      open
      wide
      onOpenChange={(o) => { if (!o) onClose(); }}
      title={<>AI 注音 · {task.name || task.media_filename}</>}
      description="设置里选的是“手动（网页聊天）”：把提示词发给任意 AI 聊天网页（Claude、ChatGPT、Gemini、DeepSeek……），再把它的完整回复粘贴回来。回复会先校验，只采用通过的行，其余保留规则读音。"
      footer={
        <>
          <Button variant="ghost" className="mr-auto" loading={busy === 'skip'} disabled={!!busy} onClick={() => void send({ skip: true })}>
            跳过（用规则读音）
          </Button>
          <Button variant="ghost" onClick={onClose}>稍后</Button>
          <Button variant="primary" loading={busy === 'submit'} disabled={!!busy || !reply.trim()}
            title={reply.trim() ? undefined : '先粘贴 AI 的回复'} onClick={() => void send({ text: reply })}>
            提交并继续
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <section className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[13px] font-semibold">① 复制提示词</span>
            <span className="text-xs text-muted">共 {task.readings_request?.lines ?? '?'} 行歌词</span>
            <Button size="sm" variant={copied ? 'secondary' : 'primary'} className="ml-auto" disabled={!prompt}
              icon={copied ? <Check className="size-4" /> : <ClipboardCopy className="size-4" />} onClick={() => void copy()}>
              {copied ? '已复制' : '复制提示词'}
            </Button>
          </div>
          {loadError ? <Callout tone="danger" title="读取提示词失败">{loadError}</Callout>
            : prompt === null ? <div className="flex items-center gap-2 text-xs text-muted"><Loader2 className="size-3.5 animate-spin" />正在读取提示词…</div>
              : <Textarea readOnly value={prompt} className="h-24 font-mono text-xs" aria-label="提示词" onFocus={(e) => e.target.select()} />}
        </section>
        <section className="space-y-2">
          <span className="text-[13px] font-semibold">② 粘贴 AI 的回复</span>
          <Textarea value={reply} onChange={(e) => { setReply(e.target.value); setError(null); }} className="h-40 font-mono text-xs"
            aria-label="AI 的回复" placeholder="把 AI 的完整回复粘贴到这里（包含 ```json 代码块也可以）" />
          {error && <Callout tone="danger" title="这个回复不能用">{error}（可以让 AI 重新回答，或者跳过）</Callout>}
        </section>
      </div>
    </Dialog>
  );
}
