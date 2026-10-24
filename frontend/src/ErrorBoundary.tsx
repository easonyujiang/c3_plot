import { Component, type ErrorInfo, type ReactNode } from 'react';

export class ErrorBoundary extends Component<{children: ReactNode}, {failed: boolean}> {
  state = {failed: false};
  static getDerivedStateFromError() { return {failed: true}; }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error('Workspace failed', error, info.componentStack); }
  render() {
    if (this.state.failed) return <div className="fatal-error" role="alert"><h1>工作台暂时无法显示</h1><p>请重新加载页面，再次打开你的火箭档案。</p><button className="primary" onClick={()=>location.reload()}>重新加载</button></div>;
    return this.props.children;
  }
}
