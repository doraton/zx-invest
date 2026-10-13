import { Empty } from 'antd';
import './PlaceholderPage.css';

interface Props {
  title: string;
  hint?: string;
}

export default function PlaceholderPage({ title, hint }: Props) {
  return (
    <div className="placeholder-page">
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <div>
            <div className="placeholder-title">{title}</div>
            <div className="placeholder-hint">{hint ?? '该模块待实现，敬请期待'}</div>
          </div>
        }
      />
    </div>
  );
}