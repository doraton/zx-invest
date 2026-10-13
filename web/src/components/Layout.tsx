import { useState, type ReactNode } from 'react';
import Sidebar from './Sidebar';
import StockSprite from './StockSprite';
import './Layout.css';

interface Props {
  children: ReactNode;
}

export default function Layout({ children }: Props) {
  // 键盘精灵的开关状态放在 Layout：侧栏按钮和 `/` 快捷键是两个入口，
  // 共享同一个 state 才不会一个开了另一个关着。
  const [spriteOpen, setSpriteOpen] = useState(false);

  return (
    <div className="app-layout">
      <Sidebar onSearch={() => setSpriteOpen(true)} />
      <main className="app-main">{children}</main>
      {/* 全局键盘精灵：挂在 Layout 上，任意页面按 `/` 都能唤起 */}
      <StockSprite open={spriteOpen} onOpenChange={setSpriteOpen} />
    </div>
  );
}