import React, { useState } from 'react';
import { ChevronDown, MoreVertical } from 'lucide-react';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

interface PONSelectorProps {
  selectedPON: number;
  onSelectPON: (id: number) => void;
}

export const PONSelector: React.FC<PONSelectorProps> = ({ selectedPON, onSelectPON }) => {
  const pons = Array.from({ length: 14 }, (_, i) => ({
    id: i + 1,
    online: Math.floor(Math.random() * 30),
    offline: Math.floor(Math.random() * 15),
  }));

  return (
    <div className="w-full lg:w-72 flex flex-col gap-4">
      <div className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
        <button className="w-full px-5 py-4 flex items-center justify-between hover:bg-slate-50 transition-colors group">
          <div className="flex items-center gap-3">
            <div className="w-2 h-2 rounded-full bg-indigo-500 shadow-[0_0_8px_rgba(99,102,241,0.5)]" />
            <span className="font-bold text-slate-700">Slot 2</span>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex gap-1.5">
              <span className="text-[10px] font-bold px-1.5 py-0.5 bg-emerald-50 text-emerald-600 rounded">223</span>
              <span className="text-[10px] font-bold px-1.5 py-0.5 bg-rose-50 text-rose-600 rounded">113</span>
            </div>
            <ChevronDown className="w-4 h-4 text-slate-400 group-hover:text-indigo-500 transition-colors" />
          </div>
        </button>
        
        <div className="p-2 space-y-1">
          <div className="px-3 pb-2 pt-1">
            <span className="text-[10px] font-extrabold text-slate-400 uppercase tracking-[0.2em]">14 PONs Available</span>
          </div>
          <div className="max-h-[500px] overflow-y-auto custom-scrollbar px-1">
            {pons.map((pon) => (
              <button
                key={pon.id}
                onClick={() => onSelectPON(pon.id)}
                className={cn(
                  "w-full flex items-center justify-between px-3 py-2.5 rounded-xl transition-all duration-200 group",
                  selectedPON === pon.id 
                    ? "bg-indigo-50 text-indigo-700 shadow-sm" 
                    : "hover:bg-slate-50 text-slate-500 hover:text-slate-800"
                )}
              >
                <div className="flex items-center gap-3">
                  <div className={cn(
                    "w-1 h-4 rounded-full transition-all",
                    selectedPON === pon.id ? "bg-indigo-600" : "bg-transparent group-hover:bg-slate-300"
                  )} />
                  <span className="text-sm font-bold">PON {pon.id}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className={cn(
                    "text-[11px] font-bold min-w-[20px] text-center",
                    selectedPON === pon.id ? "text-indigo-600" : "text-emerald-500"
                  )}>{pon.online}</span>
                  <span className={cn(
                    "text-[11px] font-bold min-w-[20px] text-center",
                    selectedPON === pon.id ? "text-indigo-400/60" : "text-rose-400"
                  )}>{pon.offline}</span>
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};
