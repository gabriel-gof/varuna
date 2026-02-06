import React from 'react';
import { 
  Wifi, 
  WifiOff, 
  RefreshCw, 
  MoreVertical, 
  MapPin, 
  ShieldCheck,
  Zap,
  Clock,
  Users
} from 'lucide-react';

interface Client {
  id: number;
  name: string;
  serial: string;
  ip: string;
  status: 'Online' | 'Offline';
  reason?: string;
  disconnectedAt?: string;
  signal?: string;
}

interface CTOCardProps {
  id: string;
  clients: Client[];
}

export const CTODetailCard: React.FC<CTOCardProps> = ({ id, clients }) => {
  return (
    <div className="bg-white rounded-[2rem] border border-slate-100 shadow-sm overflow-hidden mb-6 group transition-all hover:shadow-xl hover:shadow-slate-200/50">
      <div className="px-8 py-5 border-b border-slate-50 bg-slate-50/30 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <div className="w-10 h-10 bg-white rounded-2xl flex items-center justify-center shadow-sm border border-slate-100">
            <ShieldCheck className="w-5 h-5 text-indigo-500" />
          </div>
          <div>
            <h3 className="text-base font-black text-slate-800 tracking-tight">CTO {id}</h3>
            <div className="flex items-center gap-3">
              <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest flex items-center gap-1">
                <Users className="w-3 h-3" />
                {clients.length} Clientes
              </span>
              <div className="w-1 h-1 rounded-full bg-slate-300" />
              <span className="text-[10px] font-bold text-emerald-500 uppercase tracking-widest">
                {clients.filter(c => c.status === 'Online').length} Ativos
              </span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <div className="flex gap-1 p-1 bg-white rounded-xl border border-slate-100">
            <button className="px-3 py-1.5 bg-indigo-50 text-indigo-600 text-[10px] font-black uppercase rounded-lg">Status</button>
            <button className="px-3 py-1.5 text-slate-400 hover:text-slate-600 text-[10px] font-black uppercase rounded-lg">Potência</button>
          </div>
          <button className="p-2 text-slate-300 hover:text-indigo-500 transition-colors">
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>

      <div className="p-0">
        <table className="w-full">
          <thead>
            <tr className="bg-slate-50/20">
              <th className="px-8 py-4 text-[10px] font-black text-slate-400 uppercase tracking-[0.15em] text-left w-16">Porta</th>
              <th className="px-8 py-4 text-[10px] font-black text-slate-400 uppercase tracking-[0.15em] text-left">Cliente / Serial</th>
              <th className="px-8 py-4 text-[10px] font-black text-slate-400 uppercase tracking-[0.15em] text-center">Status</th>
              <th className="px-8 py-4 text-[10px] font-black text-slate-400 uppercase tracking-[0.15em] text-center">Sinal</th>
              <th className="px-8 py-4 text-[10px] font-black text-slate-400 uppercase tracking-[0.15em] text-right">Ações</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-50">
            {clients.map((client, index) => (
              <tr key={client.id} className="hover:bg-slate-50/50 transition-colors group/row">
                <td className="px-8 py-4">
                  <span className="text-xs font-black text-slate-300 group-hover/row:text-indigo-400 transition-colors">{index + 1}</span>
                </td>
                <td className="px-8 py-4">
                  <div className="flex flex-col">
                    <span className="text-sm font-bold text-slate-700 leading-tight">{client.name}</span>
                    <div className="flex items-center gap-2 mt-0.5">
                      <span className="text-[10px] font-mono text-slate-400 uppercase tracking-tighter">{client.serial}</span>
                      <span className="text-[10px] font-medium text-slate-300">•</span>
                      <span className="text-[10px] font-mono text-slate-400">{client.ip}</span>
                    </div>
                  </div>
                </td>
                <td className="px-8 py-4 text-center">
                  <div className={`inline-flex items-center gap-2 px-3 py-1 rounded-full text-[10px] font-black uppercase tracking-widest ${
                    client.status === 'Online' 
                      ? 'bg-emerald-50 text-emerald-600' 
                      : 'bg-rose-50 text-rose-600'
                  }`}>
                    <div className={`w-1.5 h-1.5 rounded-full ${client.status === 'Online' ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.5)]' : 'bg-rose-500'}`} />
                    {client.status}
                  </div>
                </td>
                <td className="px-8 py-4 text-center">
                  <div className="flex flex-col items-center">
                    <div className="flex items-center gap-1">
                      <Zap className={`w-3 h-3 ${client.status === 'Online' ? 'text-amber-500' : 'text-slate-300'}`} />
                      <span className="text-xs font-bold text-slate-600">{client.signal || '---'}</span>
                    </div>
                    {client.disconnectedAt && (
                      <span className="text-[9px] text-slate-400 mt-1 flex items-center gap-1 font-medium">
                        <Clock className="w-2.5 h-2.5" />
                        {client.disconnectedAt}
                      </span>
                    )}
                  </div>
                </td>
                <td className="px-8 py-4 text-right">
                  <button className="p-2 text-slate-300 hover:text-indigo-600 hover:bg-indigo-50 rounded-xl transition-all opacity-0 group-hover/row:opacity-100">
                    <MoreVertical className="w-4 h-4" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};
