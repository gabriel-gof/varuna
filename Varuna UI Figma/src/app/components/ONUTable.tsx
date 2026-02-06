import React from "react";
import {
  Wifi,
  Search,
  Filter,
  Signal,
  ArrowUpDown,
} from "lucide-react";

export const ONUTable = () => {
  const onus = [
    {
      id: "GPON00A1",
      customer: "John Smith",
      sn: "HWTC12345678",
      olt: "DOWNTOWN-01",
      port: "0/1/2",
      rx: "-18.4 dBm",
      tx: "2.1 dBm",
      status: "online",
      distance: "1.2 km",
    },
    {
      id: "GPON00B2",
      customer: "Sarah Parker",
      sn: "HWTC22334455",
      olt: "DOWNTOWN-01",
      port: "0/1/2",
      rx: "-27.1 dBm",
      tx: "1.8 dBm",
      status: "warning",
      distance: "4.5 km",
    },
    {
      id: "GPON00C3",
      customer: "Tech Solutions Inc",
      sn: "ZTE00001111",
      olt: "UPTOWN-04",
      port: "0/2/5",
      rx: "-14.2 dBm",
      tx: "2.5 dBm",
      status: "online",
      distance: "0.8 km",
    },
    {
      id: "GPON00D4",
      customer: "Mike Johnson",
      sn: "ZTE00002222",
      olt: "UPTOWN-04",
      port: "0/2/5",
      rx: "---",
      tx: "---",
      status: "offline",
      distance: "2.1 km",
    },
    {
      id: "GPON00E5",
      customer: "Linda White",
      sn: "HWTC99887766",
      olt: "DOWNTOWN-01",
      port: "0/1/4",
      rx: "-21.8 dBm",
      tx: "2.0 dBm",
      status: "online",
      distance: "3.4 km",
    },
    {
      id: "GPON00F6",
      customer: "Robert Brown",
      sn: "HWTC44556677",
      olt: "DOWNTOWN-01",
      port: "0/1/4",
      rx: "-19.5 dBm",
      tx: "2.2 dBm",
      status: "online",
      distance: "2.8 km",
    },
  ];

  return (
    <div className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
      <div className="p-6 border-b border-gray-50 space-y-4">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <h3 className="text-lg font-bold text-gray-900">
            ONU Monitoring
          </h3>
          <div className="flex items-center gap-2">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                type="text"
                placeholder="Search SN, Customer..."
                className="pl-10 pr-4 py-2 bg-gray-50 border-none rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 w-64 outline-none"
              />
            </div>
            <button className="p-2 bg-gray-50 hover:bg-gray-100 rounded-lg text-gray-600 transition-colors">
              <Filter className="w-5 h-5" />
            </button>
          </div>
        </div>

        <div className="flex gap-2">
          {[
            "All",
            "Online",
            "Warning",
            "Offline",
            "Dying Gasp",
          ].map((filter) => (
            <button
              key={filter}
              className={`px-3 py-1.5 rounded-full text-xs font-semibold transition-colors ${
                filter === "All"
                  ? "bg-indigo-600 text-white"
                  : "bg-gray-100 text-gray-600 hover:bg-gray-200"
              }`}
            >
              {filter}
            </button>
          ))}
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left">
          <thead>
            <tr className="bg-gray-50/50">
              <th className="px-6 py-4 text-xs font-semibold text-gray-500 uppercase tracking-wider">
                Customer / SN
              </th>
              <th className="px-6 py-4 text-xs font-semibold text-gray-500 uppercase tracking-wider text-center">
                OLT/Port
              </th>
              <th className="px-6 py-4 text-xs font-semibold text-gray-500 uppercase tracking-wider flex items-center gap-1">
                Rx Power <ArrowUpDown className="w-3 h-3" />
              </th>
              <th className="px-6 py-4 text-xs font-semibold text-gray-500 uppercase tracking-wider">
                Tx Power
              </th>
              <th className="px-6 py-4 text-xs font-semibold text-gray-500 uppercase tracking-wider">
                Distance
              </th>
              <th className="px-6 py-4 text-xs font-semibold text-gray-500 uppercase tracking-wider">
                Status
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {onus.map((onu) => (
              <tr
                key={onu.id}
                className="hover:bg-gray-50 transition-colors group"
              >
                <td className="px-6 py-4">
                  <div className="flex flex-col">
                    <span className="font-semibold text-gray-900">
                      {onu.customer}
                    </span>
                    <span className="text-xs text-gray-500 font-mono">
                      {onu.sn}
                    </span>
                  </div>
                </td>
                <td className="px-6 py-4 text-center">
                  <div className="text-sm font-medium text-gray-700">
                    {onu.olt}
                  </div>
                  <div className="text-[10px] text-gray-400">
                    PON {onu.port}
                  </div>
                </td>
                <td className="px-6 py-4">
                  <div
                    className={`text-sm font-bold ${
                      onu.rx === "---"
                        ? "text-gray-300"
                        : parseFloat(onu.rx) < -25
                          ? "text-red-600"
                          : parseFloat(onu.rx) < -20
                            ? "text-amber-600"
                            : "text-green-600"
                    }`}
                  >
                    {onu.rx}
                  </div>
                </td>
                <td className="px-6 py-4 text-sm text-gray-600">
                  {onu.tx}
                </td>
                <td className="px-6 py-4 text-sm text-gray-600">
                  {onu.distance}
                </td>
                <td className="px-6 py-4">
                  <div
                    className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider ${
                      onu.status === "online"
                        ? "bg-green-100 text-green-700"
                        : onu.status === "warning"
                          ? "bg-amber-100 text-amber-700"
                          : "bg-red-100 text-red-700"
                    }`}
                  >
                    {onu.status}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};