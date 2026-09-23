// Step 3 (optional): readings, AI round trip and vocal separation.

import { ArrowRight } from 'lucide-react';
import { setStep, useProject } from '@/store/app';
import { Button, PageHeader } from '@/components/ui';
import { AiRoundtripCard } from './enhance/AiRoundtrip';
import { ReadingsCard } from './enhance/Readings';
import { SeparationCard } from './enhance/Separation';

export function EnhancePage() {
  const project = useProject()!;
  const next = project.mode === 'lrc' ? 'calibrate' : 'align';
  return (
    <>
      <PageHeader
        eyebrow="第 3 步（可选）"
        title="注音与人声分离"
        description="修正读音、通过网页聊天做 AI 注音，或分离人声。注音、分离与校准互不等待，可按任意顺序进行；未分离也能听原曲并校准。"
        actions={
          <Button variant="primary" onClick={() => setStep(next)} icon={<ArrowRight className="size-4" />}>
            下一步：{next === 'calibrate' ? '首音校准' : '对齐'}
          </Button>
        }
      />
      <div className="space-y-6">
        <ReadingsCard />
        <AiRoundtripCard />
        <SeparationCard />
      </div>
    </>
  );
}
