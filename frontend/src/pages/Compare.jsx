import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Compare() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const load = () => api('/过磅对照').then(setData).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  if (err) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中</div>
  const bad = data.异常 || []
  return (
    <div className="panel">
      <h2>过磅对照</h2>
      <p className={data.一致 ? 'msg' : 'err'}>
        {data.一致
          ? `一致：在途钉住 ${data.在途钉住数} = 未结磅条 ${data.未结磅条数}`
          : `不一致：在途钉住 ${data.在途钉住数}，未结磅条 ${data.未结磅条数}，异常 ${bad.length} 条`}
      </p>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
      <h3 className="subhead">半截脏写</h3>
      <div className="table-wrap">
        <table>
          <thead><tr><th>异常</th><th>回收点</th><th>地磅</th><th>磅条号</th><th>说明</th></tr></thead>
          <tbody>
            {bad.map((b, i) => (
              <tr key={i}>
                <td>{b.异常}</td><td>{b.回收点}</td><td>{b.地磅}</td>
                <td>{b.磅条号 ?? '-'}</td><td>{b.说明}</td>
              </tr>
            ))}
            {bad.length === 0 && <tr><td colSpan="5" className="muted">无异常</td></tr>}
          </tbody>
        </table>
      </div>
      <h3 className="subhead">秤位 / 钉住 / 磅条 / 重量 / 回收态势 同刷</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>回收点</th><th>回收点状态</th><th>地磅</th><th>秤位态</th><th>有钉住</th>              <th>磅条号</th><th>磅条状态</th><th>营业日</th><th>班次</th><th>毛重</th><th>皮重</th><th>扣杂</th><th>净重</th><th>审计条数</th><th>可接新磅</th>
            </tr>
          </thead>
          <tbody>
            {(data.秤位 || []).map((r) => (
              <tr key={r.地磅编号}>
                <td>{r.回收点}</td><td>{r.回收点状态}</td><td>{r.地磅}</td><td>{r.秤位态}</td>
                <td>{r.有钉住 ? '是' : '否'}</td><td>{r.磅条号 ?? '-'}</td><td>{r.磅条状态 ?? '-'}</td>
                <td>{r.营业日 ?? '-'}</td><td>{r.班次 ?? '-'}</td>
                <td>{r.毛重 ?? '-'}</td><td>{r.皮重 ?? '-'}</td><td>{r.扣杂 ?? '-'}</td><td>{r.净重 ?? '-'}</td>
                <td>{r.审计条数}</td><td>{r.可接新磅 ? '是' : '否'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
