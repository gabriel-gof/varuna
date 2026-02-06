import React, { useState } from 'react';
import { ChevronDown, Server, Cable, Search, Filter, Cpu, CircuitBoard } from 'lucide-react';
import { useTranslation } from 'react-i18next';

interface NodeProps {
  type: 'olt' | 'slot' | 'pon';
  label: string;
  isOpen?: boolean;
  onToggle?: () => void;
  active?: boolean;
  children?: React.ReactNode;
  stats?: {
    online: number;
    dyingGasp: number;
    linkLoss: number;
    unknown: number;
  };
  sublabel?: string;
}

const NetworkNode: React.FC<NodeProps> = ({ type, label, isOpen, onToggle, active, children, stats, sublabel }) => {
  // Determine "Visual Active" state: PONs use 'active', OLT/Slots use 'isOpen'
  const isVisualActive = type === 'pon' ? active : isOpen;

  const icons = {
    olt: Server,
    slot: CircuitBoard,
    pon: Cable
  };
  const Icon = icons[type];

  // Unified card style for all node types to ensure exact same dimensions
  const cardStyle = "w-[250px] h-[74px] rounded-[18px]";

  return (
    <div className="flex flex-col relative">
      <div 
        onClick={onToggle}
        className={`
          relative flex items-center gap-3 p-3 bg-white dark:bg-slate-900 border transition-all duration-300 cursor-pointer group/node shrink-0
          ${cardStyle}
          ${isVisualActive 
            ? 'border-emerald-500/30 ring-4 ring-emerald-500/5 shadow-lg shadow-emerald-500/5' 
            : 'border-slate-100 dark:border-slate-800 hover:border-slate-200 dark:hover:border-slate-700 shadow-sm'}
        `}
      >
        {/* THE DETAIL: Vertical accent bar on the left middle */}
        <div className={`
          absolute left-0 top-1/2 -translate-y-1/2 w-1 h-8 rounded-r-full transition-all duration-300
          ${isVisualActive ? 'bg-emerald-500 scale-y-100' : 'bg-slate-100 dark:bg-slate-800 group-hover/node:bg-slate-300 scale-y-50'}
        `} />

        {/* Polished Icon Box - Changes to green when active/open */}
        <div className={`
          flex-shrink-0 w-10 h-10 flex items-center justify-center rounded-[12px] transition-all duration-300
          ${isVisualActive 
            ? 'bg-emerald-600 text-white shadow-lg shadow-emerald-600/20' 
            : 'bg-[#F8FAFB] dark:bg-slate-800 text-slate-400 group-hover/node:text-slate-500'}
        `}>
          <Icon className="w-5 h-5" />
        </div>

        <div className="flex-1 min-w-0 flex flex-col justify-center">
          <p className={`text-[13px] font-black uppercase tracking-tight leading-none mb-1.5 transition-colors ${isVisualActive ? 'text-emerald-700 dark:text-emerald-400' : 'text-slate-900 dark:text-white'}`}>
            {label}
          </p>
          
          {stats ? (
            <div className="flex items-center justify-between gap-1 w-full pr-1">
              <StatusItem color="bg-emerald-500" count={stats.online} />
              <StatusItem color="bg-blue-500" count={stats.dyingGasp} />
              <StatusItem color="bg-rose-500" count={stats.linkLoss} />
              <StatusItem color="bg-purple-500" count={stats.unknown} />
            </div>
          ) : (
            <p className="text-[9px] font-black text-slate-400 uppercase tracking-widest opacity-80">
              {sublabel}
            </p>
          )}
        </div>

        {(type === 'olt' || type === 'slot') && (
          <div className={`transition-transform duration-300 ${isOpen ? 'rotate-180 text-emerald-500' : 'text-slate-300 group-hover/node:text-slate-400'}`}>
            <ChevronDown className="w-4 h-4" />
          </div>
        )}
      </div>

      {/* Hierarchy Path - Thin and Light */}
      {isOpen && children && (
        <div className="relative mt-4 ml-6 pl-10 border-l-[1.5px] border-slate-100 dark:border-slate-800 flex flex-col gap-5 animate-in slide-in-from-top-2 duration-300">
          {children}
        </div>
      )}
    </div>
  );
};

const StatusItem = ({ color, count }: { color: string, count: number }) => (
  <div className="flex items-center gap-1 min-w-0">
    <div className={`w-2 h-2 rounded-full ${color} shadow-sm shadow-current/20 shrink-0`} />
    <span className="text-[12px] font-bold text-slate-700 dark:text-slate-200 tabular-nums leading-none">{count}</span>
  </div>
);

export const NetworkTopology: React.FC<{ onPonSelect: (id: string) => void, selectedPon?: string }> = ({ onPonSelect, selectedPon }) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [openNodes, setOpenNodes] = useState<Record<string, boolean>>({
    'olt-1': true,
    'olt-1-slot-1': true,
  });

  const toggleNode = (id: string) => {
    setOpenNodes(prev => ({ ...prev, [id]: !prev[id] }));
  };

  const oltData = [
    { id: 'olt-1', label: 'OLT-FH-BSJ-01', slotCount: 5 },
    { id: 'olt-2', label: 'OLTZTE-UNA-01', slotCount: 2 },
    { id: 'olt-3', label: 'OLT-CORE-03', slotCount: 8 },
    { id: 'olt-4', label: 'OLT-REMOTE-04', slotCount: 1 },
    { id: 'olt-5', label: 'OLT-ZTE-TEST', slotCount: 4 },
  ];

  const filteredOlts = oltData.filter(olt => 
    olt.label.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const renderOlt = (id: string, label: string, slotCount: number) => (
    <div key={id} className="flex-shrink-0">
      <NetworkNode 
        type="olt" 
        label={label} 
        sublabel={`${slotCount} SLOTS`}
        isOpen={openNodes[id]}
        onToggle={() => toggleNode(id)}
      >
        {Array.from({ length: slotCount }).map((_, sIdx) => {
          const slotNum = sIdx + 1;
          const slotId = `${id}-slot-${slotNum}`;
          
          return (
            <NetworkNode 
              key={slotId}
              type="slot" 
              label={`SLOT 0${slotNum}`} 
              sublabel="16 PONS"
              isOpen={openNodes[slotId]}
              onToggle={() => toggleNode(slotId)}
            >
              {Array.from({ length: 4 }).map((_, pIdx) => {
                const ponNum = pIdx + 1;
                const ponId = `${slotId}-pon-${ponNum}`;
                return (
                  <NetworkNode 
                    key={ponId}
                    type="pon" 
                    label={`PON 0${ponNum}`} 
                    stats={{
                      online: 125 - (pIdx * 10),
                      dyingGasp: 7 + pIdx,
                      linkLoss: 4,
                      unknown: pIdx
                    }}
                    active={selectedPon === ponId}
                    onToggle={() => onPonSelect(ponId)}
                  />
                );
              })}
            </NetworkNode>
          );
        })}
      </NetworkNode>
    </div>
  );

  return (
    <div className="flex flex-col w-full min-h-full">
      {/* Search & Filter Header - Sticky/Fixed relative to scroll view */}
      <div className="sticky top-0 z-20 flex items-center gap-3 px-10 py-8 bg-[#FDFDFD]/95 dark:bg-slate-950/95 backdrop-blur-md border-b border-slate-100/50 dark:border-slate-800/50 mb-8 supports-[backdrop-filter]:bg-[#FDFDFD]/80">
        <div className="relative w-[480px] shadow-sm shadow-slate-200/50 dark:shadow-none rounded-[18px]">
          <Search className="absolute left-5 top-1/2 -translate-y-1/2 w-4.5 h-4.5 text-slate-400" />
          <input 
            type="text" 
            placeholder="Search devices, IPs or serial numbers..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full bg-[#F3F6F9] dark:bg-slate-900 border-none rounded-[18px] py-4 pl-14 pr-6 text-[13px] font-bold text-slate-600 dark:text-slate-200 focus:ring-0 transition-all placeholder:text-slate-400/70"
          />
        </div>
        
        <button className="w-[52px] h-[52px] flex items-center justify-center bg-[#F3F6F9] dark:bg-slate-900 rounded-[18px] text-slate-400 hover:text-emerald-600 transition-all">
          <Filter className="w-5 h-5" />
        </button>
      </div>

      {/* Horizontal Topology Flow - Wrapped */}
      <div className="flex flex-wrap items-start gap-12 p-10 pt-0 pb-40 animate-in fade-in duration-500">
        {filteredOlts.map(olt => renderOlt(olt.id, olt.label, olt.slotCount))}
        
        {filteredOlts.length === 0 && (
          <div className="flex flex-col items-center justify-center w-full py-20 text-slate-300">
            <Search className="w-16 h-16 mb-4 opacity-10" />
            <p className="text-[12px] font-black uppercase tracking-[0.2em]">No equipment matches your search</p>
          </div>
        )}
      </div>
    </div>
  );
};
