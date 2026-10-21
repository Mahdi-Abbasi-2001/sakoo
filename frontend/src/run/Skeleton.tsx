import { useI18n } from '../i18n';

/** A loading shape of the run page: the same blocks, in the same places, so nothing moves when the real page arrives. */
export function RunSkeleton() {
  const { t } = useI18n();
  return (
    <div className="run" role="status" aria-busy="true" aria-label={t('conn.loading')}>
      <div className="hero2">
        <div className="h-top"><div className="sk" style={{ height: 18, width: 220 }} />
          <div className="h-id"><div className="sk" style={{ width: 104, height: 104, borderRadius: '50%' }} /><div style={{ flex: 1 }}><div className="sk" style={{ height: 14, width: 260 }} /><div className="sk" style={{ height: 64, width: '80%', marginTop: 14 }} /></div></div>
          <div className="sk" style={{ height: 22, width: '60%' }} /></div>
        <div className="h-rest"><div className="sk" style={{ height: 26, width: '70%' }} /><div className="tiles">{[0, 1, 2].map((i) => <div className="tile" key={i}><div className="sk" style={{ height: 12, width: 90 }} /><div className="sk" style={{ height: 34, width: 120, marginTop: 8 }} /></div>)}</div></div>
        <div className="h-card"><div className="card pad stack"><div className="sk" style={{ height: 24, width: 160 }} /><div className="sk" style={{ height: 28, width: '90%' }} />{[0, 1, 2, 3].map((i) => <div className="sk" key={i} style={{ height: 36 }} />)}<div className="row"><div className="sk" style={{ height: 50, width: 190, borderRadius: 999 }} /><div className="sk" style={{ height: 50, width: 110, borderRadius: 999 }} /></div></div></div>
      </div>
      <div className="cols"><div className="stack"><div className="card pad"><div className="sk" style={{ height: 120 }} /></div><div className="card pad"><div className="sk" style={{ height: 240 }} /></div></div><div className="card pad"><div className="sk" style={{ height: 380 }} /></div></div>
    </div>
  );
}
