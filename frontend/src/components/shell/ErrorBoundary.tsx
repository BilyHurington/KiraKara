import { Component, type ReactNode } from 'react';
import { Button, Callout } from '@/components/ui';

/** Keeps a crash inside one page from blanking the whole app. */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidUpdate(prev: { resetKey?: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <Callout
        tone="danger"
        title="页面出错了"
        actions={<Button size="sm" onClick={() => this.setState({ error: null })}>重试</Button>}
      >
        <pre className="mt-1 text-xs whitespace-pre-wrap">{this.state.error.message}</pre>
      </Callout>
    );
  }
}
