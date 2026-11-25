import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Overview() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const load = () => api('/总览').then(setData).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  if (err) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中</div>
  const d = data.分布 || {}
  const shift = data.班次
  const pending = data.待结段 || []
  return (
    <div className="panel">
      <h2>总览</h2>
      <p className="muted">
        当前班次：{shift ? `${shift.营业日} ${shift.班次名} · ${shift.状态}` : '无班次'}
      </p>
      <div className="grid">
        {Object.entries(d).map(([k, v]) => (
          <div className="stat" key={k}><div className="n">{v}</div><div className="l">{k}</div></div>
        ))}
      </div>
      <h3 className="subhead">回收态势 · 待结段</h3>
      <div className="table-wrap">
        <table>
          <thead><tr><th>磅条号</th><th>回收点</th><th>地磅</th><th>钉住</th><th>状态</th><th>净重</th></tr></thead>
          <tbody>
            {pending.map((r) => (
              <tr key={r.编号}>
                <td>{r.磅条号}</td><td>{r.回收点}</td><td>{r.地磅}</td>
                <td>{r.钉住}</td><td>{r.状态}</td><td>{r.净重}</td>
              </tr>
            ))}
            {pending.length === 0 && <tr><td colSpan="6" className="muted">无未结磅条</td></tr>}
          </tbody>
        </table>
      </div>
      <h3 className="subhead">地磅钉住态</h3>
      <div className="table-wrap">
        <table>
          <thead><tr><th>回收点</th><th>地磅</th><th>状态</th><th>钉住</th><th>在途磅条</th><th>可接新磅</th><th>回收点状态</th></tr></thead>
          <tbody>
            {(data.地磅 || []).map((c) => (
              <tr key={c.编号}>
                <td>{c.回收点}</td><td>{c.地磅}</td><td>{c.状态}</td><td>{c.钉住}</td>
                <td>{c.在途磅条编号 ?? '-'}</td><td>{c.可接新磅 ? '是' : '否'}</td><td>{c.回收点状态}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
