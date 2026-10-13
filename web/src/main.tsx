import { createRoot } from 'react-dom/client'
import './index.css'
// antd 全局暗色覆盖（项目没包 darkAlgorithm，靠这层把 Select/Radio/Modal 等拉暗）。
// 必须在 index.css 之后引入：本文件里的 :root 变量覆盖要压过 index.css 的基础底色。
import './theme/darkAntd.css'
import App from './App.tsx'

// 注意：klinecharts v10 在同一 DOM 上重复 init 会报 "has been initialized"，
// 与 React StrictMode 的双 mount 冲突，故关闭 StrictMode。
createRoot(document.getElementById('root')!).render(<App />)
