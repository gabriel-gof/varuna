import React, { useState, useEffect } from 'react';
import { 
  LayoutDashboard, 
  Network, 
  Settings, 
  ChevronRight,
  RotateCcw,
  LogOut,
  User,
  Search,
  ChevronDown,
  Moon,
  Sun,
  Globe
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import './i18n';
import { NetworkTopology } from './components/NetworkTopology';
import { Dashboard } from './components/Dashboard';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';

const VarunaIcon = ({ className }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" className={className}>
    <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="2.5"/>
    <circle cx="12" cy="12" r="7" stroke="currentColor" strokeWidth="1.5" strokeDasharray="2 2"/>
    <circle cx="12" cy="12" r="10.5" stroke="currentColor" strokeWidth="1" strokeDasharray="1 3" opacity="0.4"/>
    <path d="M12 2V4M12 20V22M2 12H4M20 12H22M4.93 4.93L6.34 6.34M17.66 17.66L19.07 19.07M4.93 19.07L6.34 17.66M17.66 6.34L19.07 4.93" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
  </svg>
);

const SegmentedControl = ({ options, value, onChange }: { options: { id: string, label: string }[], value: string, onChange: (id: string) => void }) => (
  <div className="flex bg-slate-100 dark:bg-slate-800 p-1 rounded-xl w-full">
    {options.map((opt) => (
      <button
        key={opt.id}
        onClick={(e) => {
          e.stopPropagation();
          onChange(opt.id);
        }}
        className={`flex-1 py-2 px-2 text-[10px] font-black uppercase tracking-wider rounded-lg transition-all ${
          value === opt.id 
            ? 'bg-white dark:bg-slate-700 text-emerald-600 shadow-sm' 
            : 'text-slate-400 hover:text-slate-600 dark:hover:text-slate-200'
        }`}
      >
        {opt.label}
      </button>
    ))}
  </div>
);

const App: React.FC = () => {
  const { t, i18n } = useTranslation();
  const [selectedPon, setSelectedPon] = useState<string | null>(null);
  const [isDarkMode, setIsDarkMode] = useState(false);
  const [activeTab, setActiveTab] = useState<'status' | 'power'>('status');
  const [activeNav, setActiveNav] = useState<'dashboard' | 'topology'>('dashboard');

  useEffect(() => {
    if (isDarkMode) {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  }, [isDarkMode]);

  const mockOnus = [
    { id: 1, port: '1', name: 'fabiano.peclat079', serial: 'DACM91F7A54E', status: 'ONLINE' as const, reason: '-', disconnectedAt: '-' },
    { id: 2, port: '2', name: 'Empresa Alpha Ltda', serial: 'DACMD5004D4', status: 'ONLINE' as const, reason: '-', disconnectedAt: '-' },
    { id: 3, port: '3', name: 'Setor Financeiro B1', serial: 'TPLG022FB450', status: 'ONLINE' as const, reason: '-', disconnectedAt: '-' },
    { id: 4, port: '4', name: 'Cond. Vila Verde P-04', serial: 'DACM91FC14E6', status: 'DYING GASP' as const, reason: 'POWER LOSS', disconnectedAt: '05/02/2026 09:15' },
    { id: 5, port: '5', name: 'Market Express Center', serial: 'VSIL00F98EC5', status: 'ONLINE' as const, reason: '-', disconnectedAt: '-' },
    { id: 6, port: '6', name: 'Clinica Saude+', serial: 'DACM77665544', status: 'OFFLINE' as const, reason: 'LINK LOSS', disconnectedAt: '05/02/2026 11:20' },
  ];

  return (
    <div className="min-h-screen bg-[#FDFDFD] dark:bg-slate-950 flex flex-col font-sans transition-colors duration-300">
      {/* Universal Top Navbar */}
      <nav className="h-16 bg-white dark:bg-slate-900 border-b border-slate-100 dark:border-slate-800 flex items-center px-6 sticky top-0 z-[100] transition-colors shadow-sm">
        <div className="flex items-center gap-3 mr-4 sm:mr-10">
          <div className="w-9 h-9 bg-emerald-600 rounded-lg flex items-center justify-center shadow-lg shadow-emerald-500/20">
            <VarunaIcon className="w-6 h-6 text-white" />
          </div>
          <span className="text-[12px] font-black text-slate-900 dark:text-white tracking-widest uppercase hidden md:block">VARUNA</span>
        </div>

        <div className="flex items-center gap-1 h-full">
          <button 
            onClick={() => setActiveNav('dashboard')}
            className={`flex items-center gap-2.5 px-4 h-full transition-all relative group ${activeNav === 'dashboard' ? 'text-emerald-600' : 'text-slate-400 hover:text-slate-600 dark:hover:text-slate-200'}`}
          >
            <LayoutDashboard className="w-4.5 h-4.5" />
            <span className="text-[11px] font-black uppercase tracking-wider hidden sm:block">{t('Dashboard')}</span>
            {activeNav === 'dashboard' && <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-emerald-600 rounded-t-full" />}
          </button>
          <button 
            onClick={() => setActiveNav('topology')}
            className={`flex items-center gap-2.5 px-4 h-full transition-all relative group ${activeNav === 'topology' ? 'text-emerald-600' : 'text-slate-400 hover:text-slate-600 dark:hover:text-slate-200'}`}
          >
            <Network className="w-4.5 h-4.5" />
            <span className="text-[11px] font-black uppercase tracking-wider hidden sm:block">{t('Topology')}</span>
            {activeNav === 'topology' && <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-emerald-600 rounded-t-full" />}
          </button>
        </div>

        <div className="flex items-center gap-3 ml-auto">
          <DropdownMenu.Root>
            <DropdownMenu.Trigger asChild>
              <button className="flex items-center gap-2.5 p-1.5 rounded-xl hover:bg-slate-50 dark:hover:bg-slate-800 transition-all group outline-none">
                <div className="w-8 h-8 rounded-lg bg-emerald-100 dark:bg-emerald-900/30 flex items-center justify-center text-emerald-600 transition-colors group-hover:bg-emerald-200 dark:group-hover:bg-emerald-800/40">
                  <User className="w-4.5 h-4.5" />
                </div>
                <ChevronDown className="w-3.5 h-3.5 text-slate-400 transition-transform duration-200 group-data-[state=open]:rotate-180" />
              </button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content 
                className="min-w-[280px] bg-white dark:bg-slate-900 rounded-2xl p-2 shadow-2xl border border-slate-100 dark:border-slate-800 z-[200] animate-in fade-in zoom-in-95 duration-200"
                sideOffset={8}
                align="end"
              >
                <div className="px-4 py-3 mb-2 border-b border-slate-100 dark:border-slate-800">
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-full bg-emerald-100 dark:bg-emerald-900/30 flex items-center justify-center text-emerald-600">
                      <User className="w-5 h-5" />
                    </div>
                    <div>
                      <p className="text-[13px] font-black text-slate-900 dark:text-white leading-none mb-1">Administrator</p>
                      <p className="text-[10px] font-bold text-slate-400">admin@varuna.net</p>
                    </div>
                  </div>
                </div>

                <div className="px-3 py-2 space-y-4">
                  <div>
                    <span className="text-[9px] font-black text-slate-400 uppercase tracking-widest block mb-2 px-1">{t('THEME')}</span>
                    <SegmentedControl 
                      value={isDarkMode ? 'dark' : 'light'}
                      onChange={(val) => setIsDarkMode(val === 'dark')}
                      options={[{ id: 'light', label: t('LIGHT') }, { id: 'dark', label: t('DARK') }]}
                    />
                  </div>
                  <div>
                    <span className="text-[9px] font-black text-slate-400 uppercase tracking-widest block mb-2 px-1">{t('LANGUAGE')}</span>
                    <SegmentedControl 
                      value={i18n.language}
                      onChange={(val) => i18n.changeLanguage(val)}
                      options={[{ id: 'en', label: 'ENGLISH' }, { id: 'pt', label: 'PORTUGUÊS' }]}
                    />
                  </div>
                </div>
                <DropdownMenu.Separator className="h-px bg-slate-100 dark:bg-slate-800 my-2 mx-2" />
                <DropdownMenu.Item className="flex items-center gap-3 px-3 py-2.5 text-[11px] font-black text-rose-500 rounded-xl cursor-pointer outline-none transition-colors hover:bg-rose-50 dark:hover:bg-rose-900/20 uppercase group">
                  <div className="w-8 h-8 rounded-lg bg-rose-100 dark:bg-rose-900/30 flex items-center justify-center text-rose-500 group-hover:bg-rose-200 dark:group-hover:bg-rose-800/50 transition-colors">
                    <LogOut className="w-4 h-4" />
                  </div>
                  <span>{t('LOGOUT')}</span>
                </DropdownMenu.Item>
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>
        </div>
      </nav>

      <main className="flex-1 flex relative overflow-hidden">
        <section className="flex-1 overflow-y-auto custom-scrollbar">
          {activeNav === 'dashboard' ? (
            <Dashboard />
          ) : (
            <NetworkTopology selectedPon={selectedPon || undefined} onPonSelect={(id) => setSelectedPon(prev => prev === id ? null : id)} />
          )}
        </section>

        {activeNav === 'topology' && (
          <aside className={`
            bg-white dark:bg-slate-900 border-l border-slate-100 dark:border-slate-800 shadow-xl transition-all duration-500 overflow-hidden h-full flex-shrink-0
            ${selectedPon ? 'w-[600px] opacity-100' : 'w-0 opacity-0 border-l-0'}
          `}>
            <div className="w-[600px] h-full overflow-y-auto custom-scrollbar">
              {selectedPon && (
                <div className="p-6">
                  <div className="flex items-center justify-between mb-8">
                    <h3 className="text-2xl font-black text-slate-800 dark:text-white uppercase tracking-tight">{selectedPon}</h3>
                    <button onClick={() => setSelectedPon(null)} className="p-2 hover:bg-slate-50 dark:hover:bg-slate-800 rounded-xl transition-all text-slate-400">
                      <ChevronRight className="w-5 h-5" />
                    </button>
                  </div>

                  <div className="flex items-center gap-2 mb-6">
                    <button onClick={() => setActiveTab('status')} className={`px-5 py-1.5 rounded-lg text-[11px] font-black transition-all ${activeTab === 'status' ? 'bg-emerald-600 text-white' : 'bg-slate-50 dark:bg-slate-800 text-slate-400'}`}>
                      {t('Status')}
                    </button>
                    <button onClick={() => setActiveTab('power')} className={`px-5 py-1.5 rounded-lg text-[11px] font-black transition-all ${activeTab === 'power' ? 'bg-emerald-600 text-white' : 'bg-slate-50 dark:bg-slate-800 text-slate-400'}`}>
                      {t('Potência')}
                    </button>
                  </div>

                  <div className="overflow-x-auto rounded-xl border border-slate-100 dark:border-slate-800">
                    <table className="w-full text-left border-collapse">
                      <thead className="bg-slate-50/50 dark:bg-slate-800/50">
                        <tr>
                          <th className="p-3 text-[9px] font-black text-slate-400 uppercase tracking-widest">PORT</th>
                          <th className="p-3 text-[9px] font-black text-slate-400 uppercase tracking-widest">CLIENT</th>
                          <th className="p-3 text-[9px] font-black text-slate-400 uppercase tracking-widest text-center">STATUS</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                        {mockOnus.map((onu) => (
                          <tr key={onu.id} className="hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-colors">
                            <td className="p-3 text-[11px] font-black text-slate-800 dark:text-slate-200">{onu.port}</td>
                            <td className="p-3">
                              <div className="flex flex-col">
                                <span className="text-[11px] font-black text-slate-800 dark:text-white uppercase">{onu.name}</span>
                                <span className="text-[8px] font-bold text-slate-400 font-mono">{onu.serial}</span>
                              </div>
                            </td>
                            <td className="p-3 text-center">
                              <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[8px] font-black uppercase ${onu.status === 'ONLINE' ? 'bg-emerald-50 text-emerald-600' : 'bg-rose-50 text-rose-500'}`}>
                                <div className={`w-1 h-1 rounded-full ${onu.status === 'ONLINE' ? 'bg-emerald-500' : 'bg-rose-500'}`} />
                                {onu.status}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          </aside>
        )}
      </main>
    </div>
  );
};

export default App;