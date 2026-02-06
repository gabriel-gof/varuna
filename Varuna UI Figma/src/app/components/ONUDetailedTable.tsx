import React from 'react';
import { Signal, SignalLow, SignalMedium, SignalHigh, Power, Clock, MoreHorizontal } from 'lucide-react';

interface ONUData {
  id: number;
  name: string;
  serial: string;
  status: 'Ativo' | 'Inativo';
  reason?: string;
  power?: string;
  disconnectedAt?: string;
}

const mockData: ONUData[] = [
  { id: 1, name: 'ONU-01', serial: 'FHTT6A8EE821', status: 'Inativo', reason: 'Desconhecido', disconnectedAt: '2026-02-04 10:15' },
  { id: 2, name: 'ONU-02', serial: 'OPT13AB05227', status: 'Ativo', power: '-18.4 dBm' },
  { id: 4, name: 'ONU-04', serial: 'VSIL00F98EC5', status: 'Inativo', reason: 'Dying Gasp', disconnectedAt: '2026-02-04 09:42' },
  { id: 7, name: 'ONU-07', serial: 'XPON49533195', status: 'Ativo', power: '-21.2 dBm' },
  { id: 8, name: 'ONU-08', serial: 'MONU00273F61', status: 'Ativo', power: '-15.8 dBm' },
];

export const ONUDetailedTable: React.FC<{ ponId: number }> = ({ ponId }) => {
  return (
    <div className="flex-1 bg-white rounded-3xl border border-gray-100 shadow-sm overflow-hidden flex flex-col">
      <div className="px-8 py-6 border-b border-gray-50 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <h3 className="text-lg font-extrabold text-slate-800">GABISAT-OLT-ZTE</h3>
            <span className="text-slate-300">/</span>
            <span className="text-sm font-bold text-slate-400">Slot 2</span>
            <span className="text-slate-300">/</span>
            <span className="text-sm font-bold text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded-lg">PON {ponId}</span>
          </div>
          <div className="flex gap-4 items-center">
            <div className="flex items-center gap-1.5">
              <div className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
              <span className="text-xs font-bold text-slate-500">12 Online</span>
            </div>
            <div className="flex items-center gap-1.5">
              <div className="w-1.5 h-1.5 rounded-full bg-blue-500" />
              <span className="text-xs font-bold text-slate-500">1 Warning</span>
            </div>
            <div className="flex items-center gap-1.5">
              <div className="w-1.5 h-1.5 rounded-full bg-rose-500" />
              <span className="text-xs font-bold text-slate-500">0 Critical</span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2 p-1 bg-slate-100 rounded-xl">
          <button className="px-4 py-1.5 bg-white text-indigo-600 text-xs font-bold rounded-lg shadow-sm border border-slate-200">
            Topology Status
          </button>
          <button className="px-4 py-1.5 text-slate-500 hover:text-indigo-600 text-xs font-bold rounded-lg transition-colors">
            Topology Power
          </button>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="bg-slate-50/50">
              <th className="px-8 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-left">ID</th>
              <th className="px-8 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-left">Topology Name</th>
              <th className="px-8 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-left">Status</th>
              <th className="px-8 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-left">Signal/Reason</th>
              <th className="px-8 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-left">Disconnected At</th>
              <th className="px-8 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-center">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {mockData.map((onu) => (
              <tr key={onu.id} className="hover:bg-slate-50/80 transition-all group">
                <td className="px-8 py-5">
                  <span className="text-xs font-bold text-slate-400">#{onu.id}</span>
                </td>
                <td className="px-8 py-5">
                  <div className="flex flex-col">
                    <span className="text-sm font-bold text-slate-700">{onu.name}</span>
                    <span className="text-[10px] font-mono font-medium text-slate-400 tracking-wider uppercase">{onu.serial}</span>
                  </div>
                </td>
                <td className="px-8 py-5">
                  <div className={`inline-flex items-center gap-2 px-2.5 py-1 rounded-full text-[10px] font-extrabold uppercase tracking-widest ${
                    onu.status === 'Ativo' 
                      ? 'bg-emerald-50 text-emerald-600 border border-emerald-100' 
                      : 'bg-rose-50 text-rose-600 border border-rose-100'
                  }`}>
                    <div className={`w-1.5 h-1.5 rounded-full ${onu.status === 'Ativo' ? 'bg-emerald-500 animate-pulse' : 'bg-rose-500'}`} />
                    {onu.status}
                  </div>
                </td>
                <td className="px-8 py-5">
                  {onu.status === 'Ativo' ? (
                    <div className="flex items-center gap-2">
                      <SignalMedium className="w-4 h-4 text-emerald-500" />
                      <span className="text-sm font-bold text-slate-600">{onu.power}</span>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <Power className="w-3.5 h-3.5 text-rose-400" />
                      <span className="text-xs font-semibold text-rose-400/80 italic">{onu.reason}</span>
                    </div>
                  )}
                </td>
                <td className="px-8 py-5">
                  {onu.disconnectedAt ? (
                    <div className="flex items-center gap-2 text-slate-400">
                      <Clock className="w-3.5 h-3.5" />
                      <span className="text-[11px] font-medium">{onu.disconnectedAt}</span>
                    </div>
                  ) : (
                    <span className="text-[11px] text-slate-300 font-bold">—</span>
                  )}
                </td>
                <td className="px-8 py-5 text-center">
                  <button className="p-2 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-xl transition-all opacity-0 group-hover:opacity-100">
                    <MoreHorizontal className="w-5 h-5" />
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
