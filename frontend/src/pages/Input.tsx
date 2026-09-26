// Step 2: audio and lyrics input.

import { ArrowRight } from 'lucide-react';
import { setStep, useProject } from '@/store/app';
import { useDraft } from '@/store/drafts';
import { Button, PageHeader } from '@/components/ui';
import { AudioCard } from './input/AudioCard';
import { LinesTable } from './input/LinesTable';
import { LyricsInputCard } from './input/LyricsInputCard';
import { PairingCard } from './input/PairingCard';

export function InputPage() {
  const project = useProject()!;
  // kept (like the lyrics drafts below) while the page is left and come back to
  const [extraTracks, setExtraTracks] = useDraft<Record<string, string>>('input.extraTracks', {});
  const ready = project.lyrics.lines.some((l) => l.kind === 'lyric') && project.audio.some((a) => a.role === 'original');

  return (
    <>
      <PageHeader
        eyebrow="第 2 步"
        title="音频与歌词"
        description="上传原曲并输入已知歌词。歌词、LRC、prepared.json 等文本都支持直接粘贴或上传文件，解析后预览确认才会应用；解析失败不会覆盖原项目。"
        actions={(
          <Button variant={ready ? 'primary' : 'secondary'} onClick={() => setStep('enhance')} icon={<ArrowRight className="size-4" />}>
            下一步：注音与分离
          </Button>
        )}
      />
      <div className="space-y-6">
        <AudioCard />
        <LyricsInputCard onExtraTracks={setExtraTracks} />
        <PairingCard extraTracks={extraTracks} />
        <LinesTable />
      </div>
    </>
  );
}
