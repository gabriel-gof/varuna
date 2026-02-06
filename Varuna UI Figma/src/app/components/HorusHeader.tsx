import React from 'react';
import { 
  ChevronRight, 
  Search, 
  Bell, 
  User, 
  Eye, 
  LayoutGrid, 
  List, 
  Maximize2, 
  Printer, 
  X,
  Cpu,
  Server,
  Network
} from 'lucide-react';

export const HorusHeader: React.FC = () => {
  return (
    <header className="h-16 bg-white border-b border-slate-200 px-6 flex items-center justify-between sticky top-0 z-50">
      <div className="flex items-center gap-8">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 bg-indigo-600 rounded-xl flex items-center justify-center shadow-lg shadow-indigo-100">
            <Eye className="w-5 h-5 text-white" />
          </div>
          <div>
            <h1 className="text-xl font-black text-slate-800 tracking-tight leading-none">Horus</h1>
            <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mt-1">Network Intel</p>
          </div>
        </div>

        <div className="hidden md:flex relative group w-96">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 group-focus-within:text-indigo-500 transition-colors" />
          <input 
            type="text" 
            placeholder="Buscar OLT, CTO ou Cliente..." 
            className="w-full pl-10 pr-4 py-2 bg-slate-100 border-none rounded-xl text-sm focus:ring-2 focus:ring-indigo-500/20 outline-none transition-all placeholder:text-slate-400 font-medium"
          />
        </div>
      </div>

      <div className="flex items-center gap-3">
        <div className="flex items-center bg-slate-100 p-1 rounded-xl mr-4">
          <button className="p-1.5 bg-white shadow-sm rounded-lg text-indigo-600">
            <LayoutGrid className="w-4 h-4" />
          </button>
          <button className="p-1.5 text-slate-400 hover:text-slate-600">
            <List className="w-4 h-4" />
          </button>
        </div>
        
        <button className="p-2 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-xl transition-all relative">
          <Bell className="w-5 h-5" />
          <span className="absolute top-2 right-2 w-2 h-2 bg-rose-500 rounded-full border-2 border-white"></span>
        </button>
        
        <div className="h-8 w-px bg-slate-200 mx-1"></div>
        
        <div className="flex items-center gap-3 pl-2 cursor-pointer group">
          <div className="text-right hidden sm:block">
            <p className="text-xs font-bold text-slate-800 group-hover:text-indigo-600 transition-colors">NOC Operator</p>
            <p className="text-[9px] font-black text-slate-400 uppercase tracking-widest">Administrator</p>
          </div>
          <div className="w-9 h-9 rounded-xl bg-slate-800 flex items-center justify-center text-white font-bold text-sm shadow-md">
            N
          </div>
        </div>
      </div>
    </header>
  );
};

interface BreadcrumbProps {
  items: string[];
}

export const HorusBreadcrumbs: React.FC<BreadcrumbProps> = ({ items }) => {
  return (
    <div className="flex items-center gap-2 mb-6">
      {items.map((item, index) => (
        <React.Fragment key={item}>
          <span className={cn(
            "text-[10px] font-black uppercase tracking-widest",
            index === items.length - 1 ? "text-indigo-600" : "text-slate-400"
          )}>
            {item}
          </span>
          {index < items.length - 1 && <ChevronRight className="w-3 h-3 text-slate-300" />}
        </React.Fragment>
      ))}
    </div>
  );
};

function cn(...inputs: any[]) {
  return inputs.filter(Boolean).join(' ');
}
