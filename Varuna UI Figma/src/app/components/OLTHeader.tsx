import React from 'react';
import { Server, Wifi, WifiOff, Users, Cpu } from 'lucide-react';

interface OLTHeaderProps {
  name: string;
  ip: string;
  model: string;
  onlineCount: number;
  offlineCount: number;
  totalCount: number;
}

export const OLTHeader: React.FC<OLTHeaderProps> = ({ name, ip, model, onlineCount, offlineCount, totalCount }) => {
  return (
    <div className="bg-white p-6 rounded-3xl border border-gray-100 shadow-sm mb-6">
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-6">
        <div className="flex items-center gap-4">
          <div className="w-14 h-14 bg-indigo-50 rounded-2xl flex items-center justify-center">
            <Cpu className="w-8 h-8 text-indigo-600" />
          </div>
          <div>
            <div className="flex items-center gap-2 mb-1">
              <h2 className="text-xl font-extrabold text-slate-800 tracking-tight">{name}</h2>
              <span className="px-2 py-0.5 bg-slate-100 text-slate-500 text-[10px] font-bold rounded uppercase tracking-wider">{model}</span>
            </div>
            <div className="flex items-center gap-3 text-sm font-medium text-slate-400">
              <span className="flex items-center gap-1.5 font-mono">
                <div className="w-1.5 h-1.5 rounded-full bg-indigo-400" />
                {ip}
              </span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className="flex items-center gap-6 px-6 py-3 bg-slate-50 rounded-2xl border border-slate-100/50">
            <div className="text-center">
              <div className="flex items-center justify-center gap-1.5 text-emerald-600 mb-0.5">
                <Wifi className="w-4 h-4" />
                <span className="text-lg font-bold">{onlineCount}</span>
              </div>
              <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Online</span>
            </div>
            <div className="w-px h-8 bg-slate-200" />
            <div className="text-center">
              <div className="flex items-center justify-center gap-1.5 text-rose-500 mb-0.5">
                <WifiOff className="w-4 h-4" />
                <span className="text-lg font-bold">{offlineCount}</span>
              </div>
              <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Offline</span>
            </div>
            <div className="w-px h-8 bg-slate-200" />
            <div className="text-center">
              <div className="flex items-center justify-center gap-1.5 text-slate-700 mb-0.5">
                <Users className="w-4 h-4" />
                <span className="text-lg font-bold">{totalCount}</span>
              </div>
              <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Total ONUs</span>
            </div>
          </div>
          <button className="p-3 bg-white border border-slate-200 hover:border-indigo-300 hover:bg-indigo-50 text-slate-400 hover:text-indigo-600 rounded-2xl transition-all shadow-sm">
            <Wifi className="w-5 h-5" />
          </button>
        </div>
      </div>
    </div>
  );
};
