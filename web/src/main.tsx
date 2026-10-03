import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// 注意：klinecharts v10 在同一 DOM 上重复 init 会报 "has been initialized"，
// 与 React StrictMode 的双 mount 冲突，故关闭 StrictMode。
createRoot(document.getElementById('root')!).render(<App />)
