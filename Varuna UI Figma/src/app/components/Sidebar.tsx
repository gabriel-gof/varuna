import React from 'react';
import { 
  LayoutDashboard, 
  Settings, 
  Bell, 
  User, 
  ChevronLeft,
  Search,
  RefreshCw,
  AlertCircle
} from 'lucide-react';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

interface SidebarProps {
  activeTab: string;
  setActiveTab: (tab: string) => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ activeTab, setActiveTab }) => {
  const menuItems = [
    { id: 'dashboard', label: 'Painel', icon: LayoutDashboard },
    { id: 'settings', label: 'Configurações', icon: Settings },
  ];

  return (
    <div className="flex flex-col h-screen w-20 lg:w-64 bg-[#F8F9FB] border-r border-gray-200 transition-all duration-300">
      <div className="p-6 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-indigo-600 rounded-lg flex items-center justify-center shadow-lg shadow-indigo-200">
            <div className="w-4 h-4 border-2 border-white rounded-full border-t-transparent animate-spin-slow" />
          </div>
          <span className="text-xl font-bold tracking-tight text-slate-800 hidden lg:block">Varuna</span>
        </div>
        <button className="text-gray-400 hover:text-gray-600 hidden lg:block">
          <ChevronLeft className="w-5 h-5" />
        </button>
      </div>

      <nav className="flex-1 px-4 mt-4 space-y-2">
        {menuItems.map((item) => (
          <button
            key={item.id}
            onClick={() => setActiveTab(item.id)}
            className={cn(
              "w-full flex items-center gap-3 px-3 py-3 rounded-xl transition-all duration-200 group",
              activeTab === item.id 
                ? "bg-white shadow-sm border border-gray-100 text-indigo-600" 
                : "text-gray-500 hover:bg-gray-100"
            )}
          >
            <item.icon className={cn(
              "w-5 h-5 transition-transform duration-200 group-hover:scale-110",
              activeTab === item.id ? "text-indigo-600" : "text-gray-400"
            )} />
            <span className="font-semibold text-sm hidden lg:block">{item.label}</span>
            {activeTab === item.id && (
              <div className="ml-auto w-1.5 h-1.5 rounded-full bg-indigo-600 hidden lg:block" />
            )}
          </button>
        ))}
      </nav>

      <div className="p-4 space-y-4">
        <button className="w-full flex items-center gap-3 px-3 py-3 text-gray-500 hover:bg-gray-100 rounded-xl transition-all group">
          <Bell className="w-5 h-5 group-hover:rotate-12 transition-transform" />
          <span className="font-semibold text-sm hidden lg:block">Notificações</span>
        </button>
        <div className="h-px bg-gray-200 mx-2 hidden lg:block" />
        <button className="w-full flex items-center gap-3 px-3 py-3 text-gray-500 hover:bg-gray-100 rounded-xl transition-all group">
          <div className="w-6 h-6 rounded-full bg-indigo-100 flex items-center justify-center">
            <User className="w-4 h-4 text-indigo-600" />
          </div>
          <span className="font-semibold text-sm hidden lg:block">Admin</span>
        </button>
        <div className="text-[10px] text-center text-gray-400 font-bold tracking-widest hidden lg:block">v1.0</div>
      </div>
    </div>
  );
};

export const TopBar: React.FC = () => {
  return (
    <div className="h-16 flex items-center justify-between px-8 bg-white/80 backdrop-blur-md border-b border-gray-100 sticky top-0 z-10">
      <div className="flex-1 max-w-xl">
        <div className="relative group">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 group-focus-within:text-indigo-500 transition-colors" />
          <input 
            type="text" 
            placeholder="Buscar ONU por nome ou serial..." 
            className="w-full pl-10 pr-4 py-2 bg-gray-100/50 border-none rounded-xl text-sm focus:ring-2 focus:ring-indigo-500 outline-none transition-all placeholder:text-gray-400"
          />
        </div>
      </div>
      <div className="flex items-center gap-3 ml-4">
        <button className="p-2 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-xl transition-all">
          <AlertCircle className="w-5 h-5" />
        </button>
        <button className="p-2 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-xl transition-all">
          <RefreshCw className="w-5 h-5" />
        </button>
        <button className="p-2 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-xl transition-all">
          <div className="w-5 h-5 border-2 border-current rounded flex items-center justify-center text-[10px] font-bold">Z</div>
        </button>
      </div>
    </div>
  );
};
