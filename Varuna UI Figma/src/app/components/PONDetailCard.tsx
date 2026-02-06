import React from 'react';
import { 
  Wifi, 
  WifiOff, 
  RefreshCw, 
  MoreVertical, 
  ShieldCheck,
  Zap,
  Clock,
  Users,
  Activity,
  ArrowUpRight,
  ArrowDownRight
} from 'lucide-react';

interface ONU {
  id: number;
  name: string;
  serial: string;
  ip: string;
  status: 'Online' | 'Offline';
  signal?: string;
  uptime?: string;
}

interface PONCardProps {
  id: string;
  olt: string;
  slot: string;
  onus: ONU[];
}

export const PONDetailCard: React.FC<PONCardProps> = ({ id, olt, slot, onus }) => {
  const onlineCount = onus.filter(o => o.status === 'Online').length;
  const offlineCount = onus.length - onlineCount;

  return (
    <div className="bg-white rounded-[2rem] border border-slate-200/60 shadow-sm overflow-hidden mb-8 group transition-all hover:shadow-xl hover:shadow-indigo-500/5">
      <div className="px-8 py-6 border-b border-slate-50 bg-slate-50/20 flex items-center justify-between">
        <div className="flex items-center gap-5">
          <div className="w-12 h-12 bg-indigo-600 rounded-2xl flex items-center justify-center shadow-lg shadow-indigo-100 border-2 border-white">
            <Activity className="w-6 h-6 text-white" />
          </div>
          <div>
            <div className="flex items-center gap-2 mb-1">
              <h3 className="text-lg font-black text-slate-800 tracking-tight">{id}</h3>
              <span className="text-slate-300">•</span>
              <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest">{olt}</span>
              <span className="text-slate-300">/</span>
              <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest">{slot}</span>
            </div>
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-1.5">
                <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                <span className="text-[10px] font-black text-emerald-600 uppercase tracking-widest">{onlineCount} Online</span>
              </div>
              <div className="flex items-center gap-1.5">
                <div className="w-1.5 h-1.5 rounded-full bg-rose-500" />
                <span className="text-[10px] font-black text-rose-500 uppercase tracking-widest">{offlineCount} Offline</span>
              </div>
              <div className="h-3 w-px bg-slate-200 mx-1" />
              <div className="flex items-center gap-1.5">
                <ArrowDownRight className="w-3 h-3 text-indigo-400" />
                <span className="text-[10px] font-black text-slate-500 uppercase tracking-widest">2.4 Gbps</span>
                <ArrowUpRight className="w-3 h-3 text-indigo-400" />
                <span className="text-[10px] font-black text-slate-500 uppercase tracking-widest">1.2 Gbps</span>
              </div>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className="flex gap-1 p-1 bg-white border border-slate-200 rounded-xl">
            <button className="px-4 py-2 bg-indigo-50 text-indigo-600 text-[10px] font-black uppercase rounded-lg">Performance</button>
            <button className="px-4 py-2 text-slate-400 hover:text-slate-600 text-[10px] font-black uppercase rounded-lg">Config</button>
          </div>
          <button className="p-2.5 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-xl transition-all border border-slate-100 bg-white">
            <RefreshCw className="w-4.5 h-4.5" />
          </button>
          <button className="p-2.5 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-xl transition-all">
            <MoreVertical className="w-5 h-5" />
          </button>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="bg-slate-50/50">
              <th className="px-8 py-5 text-[10px] font-black text-slate-400 uppercase tracking-[0.2em] text-left">Subscriber</th>
              <th className="px-8 py-5 text-[10px] font-black text-slate-400 uppercase tracking-[0.2em] text-left">Hardware ID</th>
              <th className="px-8 py-5 text-[10px] font-black text-slate-400 uppercase tracking-[0.2em] text-center">Connection</th>
              <th className="px-8 py-5 text-[10px] font-black text-slate-400 uppercase tracking-[0.2em] text-center">RX Signal</th>
              <th className="px-8 py-5 text-[10px] font-black text-slate-400 uppercase tracking-[0.2em] text-center">Uptime</th>
              <th className="px-8 py-5 text-[10px] font-black text-slate-400 uppercase tracking-[0.2em] text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {onus.map((onu) => (
              <tr key={onu.id} className="hover:bg-slate-50/60 transition-colors group/row">
                <td className="px-8 py-5">
                  <div className="flex flex-col">
                    <span className="text-sm font-bold text-slate-800 tracking-tight">{onu.name}</span>
                    <span className="text-[10px] font-medium text-slate-400 mt-0.5">{onu.ip}</span>
                  </div>
                </td>
                <td className="px-8 py-5">
                  <span className="text-[11px] font-mono text-slate-500 font-bold uppercase tracking-tighter bg-slate-50 px-2 py-0.5 rounded border border-slate-100">{onu.serial}</span>
                </td>
                <td className="px-8 py-5 text-center">
                  <div className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-xl text-[10px] font-black uppercase tracking-widest ${
                    onu.status === 'Online' 
                      ? 'bg-emerald-50 text-emerald-600' 
                      : 'bg-rose-50 text-rose-600'
                  }`}>
                    <div className={`w-1.5 h-1.5 rounded-full ${onu.status === 'Online' ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.4)]' : 'bg-rose-500'}`} />
                    {onu.status}
                  </div>
                </td>
                <td className="px-8 py-5 text-center">
                  <div className="flex items-center justify-center gap-1.5">
                    <Zap className={`w-3.5 h-3.5 ${onu.status === 'Online' ? 'text-indigo-400' : 'text-slate-200'}`} />
                    <span className={`text-xs font-black ${onu.status === 'Online' ? 'text-slate-700' : 'text-slate-300'}`}>{onu.signal || '---'}</span>
                  </div>
                </td>
                <td className="px-8 py-5 text-center">
                  <span className="text-[11px] font-bold text-slate-500 tracking-tight">{onu.uptime || '--'}</span>
                </td>
                <td className="px-8 py-5 text-right">
                  <button className="p-2 text-slate-300 hover:text-indigo-600 hover:bg-indigo-50 rounded-xl transition-all opacity-0 group-hover/row:opacity-100">
                    <MoreVertical className="w-5 h-5" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="px-8 py-4 bg-slate-50/50 border-t border-slate-100 flex items-center justify-center">
        <button className="text-[10px] font-black text-indigo-600 uppercase tracking-widest hover:text-indigo-700 transition-colors">
          View All {onus.length} Subscribers
        </button>
      </div>
    </div>
  );
};
