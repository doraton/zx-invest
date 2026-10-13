/**
 * 除息除权记录弹窗。
 *
 * 点主图下方的 `$` 标记触发：列出该标的**全部**除权日，高亮本次点中的那条。
 *
 * 两条设计约束：
 * 1. 列表骨架来自后端按 `pre_close` 判定的**真实除权日**，不是 `stock_xdxr` 全表
 *    （后者混着大量「股本变化 / 送配股上市」，不影响价格，混进来每条都点不开）。
 * 2. 「每10股」这层话术在后端拼好（`server/market.py::_xdxr_text`），
 *    因为库里存的是源口径（pytdx 就是每 10 股），换算属于展示层的事，
 *    放一处做才不会和库里口径漂移。
 */
import { useEffect, useRef, useState } from 'react';
import { Modal } from 'antd';
import { fetchXdxr, type XdxrRecord } from '../config/api';
import { formatSymbolCode } from '../utils/formatCode';
import './XdxrModal.css';

interface Props {
  open: boolean;
  code: string;
  name: string;
  /** 本次点中的除权日；null = 不是点标记进来的（未来可能从别处打开）。 */
  activeDate: string | null;
  onClose: () => void;
}

export default function XdxrModal({ open, code, name, activeDate, onClose }: Props) {
  const [rows, setRows] = useState<XdxrRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 打开时把高亮那条滚进视野。用 ref 在数据到位后触发，而不是在 JSX 里写
  // scrollIntoView —— 那要等 antd 弹窗动画跑完才量得到高度。
  const activeRef = useRef<HTMLLIElement | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchXdxr(code)
      .then((res) => {
        if (cancelled) return;
        if (res.code !== 0) {
          setError(res.error || '除权除息记录加载失败');
          setRows([]);
        } else {
          setRows(res.data || []);
        }
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, code]);

  // 记录到位后把高亮行滚到可视区。放一个 rAF 是等 antd 把弹窗挂上并量完高度。
  useEffect(() => {
    if (!open || loading || !activeDate) return;
    const raf = requestAnimationFrame(() => {
      activeRef.current?.scrollIntoView({ block: 'center' });
    });
    return () => cancelAnimationFrame(raf);
  }, [open, loading, activeDate, rows]);

  // 切标的时清掉上一只票的记录，避免新弹窗先闪一下旧数据。
  useEffect(() => {
    if (!open) {
      setRows([]);
      setError(null);
    }
  }, [open]);

  return (
    <Modal
      open={open}
      onCancel={onClose}
      footer={null}
      title={`${name}(${formatSymbolCode(code).slice(-6)})`}
      width={520}
      destroyOnHidden
    >
      {loading ? (
        <div className="xdxr-hint">加载中…</div>
      ) : error ? (
        <div className="xdxr-hint xdxr-hint-error">{error}</div>
      ) : rows.length === 0 ? (
        <div className="xdxr-hint">该标的没有除息除权记录</div>
      ) : (
        <ul className="xdxr-list">
          {rows.map((r) => {
            const active = r.date === activeDate;
            return (
              <li
                key={r.date}
                ref={active ? activeRef : undefined}
                className={`xdxr-row${active ? ' is-active' : ''}`}
              >
                <span className="xdxr-date">{r.date}</span>
                <span className="xdxr-text">{r.text || '—'}</span>
                {/* 涨跌幅口径与库里一致（基准是已除权前收），复权模式下也成立 */}
                <span className="xdxr-prev" title={`除权前收盘 ${r.pre_close}`}>
                  {r.pre_close.toFixed(2)}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </Modal>
  );
}