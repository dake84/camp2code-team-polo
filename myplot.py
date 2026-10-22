#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Jul 18 08:37:56 2023

@author: janmeyer
"""

"Definiere Befehl: Alle Variabeln lÃ¶schen"
def clear_all():
    """Clears all the variables from the workspace of the spyder application."""
    gl = globals().copy()
    for var in gl:
        if var[0] == '_': continue
        if 'func' in str(globals()[var]): continue
        if 'module' in str(globals()[var]): continue

        del globals()[var]
if __name__ == "__main__":
    clear_all()

import os
absFilePath = os.path.abspath(__file__) # nimmt den Filepath des skripts
filepath, filename = os.path.split(absFilePath) # Teilt den abosluten Path auf in Path und Dateiname
os.chdir(filepath)

import matplotlib.font_manager
from IPython.core.display import HTML

"Pakete einbinden"
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import curve_fit
from PIL import Image

def format_output(for_in):
    return '%.4e' % for_in

def conv(x):
    return x.replace(',', '.').encode()

class Plot:
    def __init__(self):
        self.color = '#97d9e7'
        self.fig_Name = 'output'
        self.save_plotting = False
        self.fontfamily = 'sans' # Cambria geht leider nicht
        self.font = {'family': self.fontfamily,
                'color':  'black',
                'weight': 'normal',
                'size': 16,
                }
        self.c_HF = '#97d9e7'
        self.c_LF = '#E64B35FF'
        self.log_x = False
        self.log_y = False
        self.log_y2 = False
        self.legend_loc = 'upper right'
        self.x_axe_name = 'Zeit $t$ [s]'
        self.y_axe_name = 'Ort $y$ [m]'
        self.y2_axe_name = 'Ort $y$ [m]'
        self.label = 'Graph'
        self.linestyle = '-'
        self.x_max = 1
        self.x_min = 0
        self.y_max = 1
        self.y_min = 0
        self.x_max2 = 1
        self.x_min2 = 0
        self.y_max2 = 1
        self.y_min2 = 0
        self.BOOL_1Graph = True
        self.x_in = [1,2,3]
        self.y_in = [1,2,3]
        self.which_y_axis = ['y1', 'y2']
        self.y2_style = 'sci'
        
    def input_data(self, data, *args):
        if len(args) > 0:
            self.BOOL_1Graph = False
            x_out = [data[0]]
            y_out = [data[1]]
            for i in range(len(args)):
                    x_out.append(args[i][0])
                    y_out.append(args[i][1])
        else:
            self.BOOL_1Graph = True
            x_out = data[0]
            y_out = data[1]
        return x_out, y_out

    def use_meta_data(self, data, *args):
        
        if len(args) > 0:
            self.BOOL_1Graph = False
            x_out = [data[0]]
            y_out = [data[1]]
            which_y_axis = [data[4]]
            for i in range(len(args)):
                x_out.append(args[i][0])
                y_out.append(args[i][1])
                try:
                    which_y_axis.append(args[i][4])
                except:
                    which_y_axis.append('y1')
                        
            
        else:
            self.BOOL_1Graph = True
            x_out = data[0]
            y_out = data[1]
            
            try:
                which_y_axis = [data[4]]
            except:
                which_y_axis = ['y1']
            
        self.x_in = x_out
        self.y_in = y_out
        self.which_y_axis = which_y_axis
        
        if len(args) > 0:
            label_out = [data[2]]
            color_out = [data[3]]
            linestyle_out = [data[5]]
            for i in range(len(args)):
                if args[i][3] == None:
                    color_out.append(None)
                else:
                    color_out.append(args[i][3])
                if args[i][2] == None:
                    label_out.append(None)
                else:
                    label_out.append(args[i][2])
                if args[i][5] == None:
                    linestyle_out.append(None)
                else:
                    linestyle_out.append(args[i][5])
        else:
            label_out = data[2]
            color_out = data[3]
            linestyle_out = data[5]
            
        self.label = label_out
        self.color = color_out
        self.linestyle = linestyle_out
        
        return label_out, color_out      
        
    def x_y_lim_y1(self, x_in, y_in): #Erstellt die limit werte für den Graphen
        if len(self.which_y_axis) > 1:
            x_max_arr = []
            x_min_arr = []
            y_max_arr = []
            y_min_arr = []
            for i in range(len(x_in)):
                if self.which_y_axis[i] == 'y1':
                    x_max_arr.append(np.max(x_in[i]))
                    x_min_arr.append(np.min(x_in[i]))
                    y_max_arr.append(np.max(y_in[i]))
                    y_min_arr.append(np.min(y_in[i]))

            x_max = np.max(x_max_arr)
            x_min = np.min(x_min_arr)
            y_max = np.max(y_max_arr)
            y_min = np.min(y_min_arr)
        else:
            x_max = np.max(x_in)
            x_min = np.min(x_in)
            y_max = np.max(y_in)
            y_min = np.min(y_in)
        
        if self.x_max == 1:
            self.x_max = x_max
        if self.x_min == 0:
            self.x_min = x_min
        if self.y_max == 1:
            self.y_max = y_max
        if self.y_min == 0:
            self.y_min = y_min
        
    def x_y_lim_y2(self, x_in, y_in): #Erstellt die limit werte für den Graphen
        if len(x_in) > 1:
            x_max_arr = []
            x_min_arr = []
            y_max_arr = []
            y_min_arr = []
            for i in range(len(x_in)):
                if self.which_y_axis[i] == 'y2':
                    x_max_arr.append(np.max(x_in[i]))
                    x_min_arr.append(np.min(x_in[i]))
                    y_max_arr.append(np.max(y_in[i]))
                    y_min_arr.append(np.min(y_in[i]))
            
            x_max = np.max(x_max_arr)
            x_min = np.min(x_min_arr)
            y_max = np.max(y_max_arr)
            y_min = np.min(y_min_arr)
        else:
            x_max = np.max(x_in)
            x_min = np.min(x_in)
            y_max = np.max(y_in)
            y_min = np.min(y_in)
        
        if self.x_max2 == 1:
            self.x_max2 = x_max
        if self.x_min2 == 0:
            self.x_min2 = x_min
        if self.y_max2 == 1:
            self.y_max2 = y_max
        if self.y_min2 == 0:
            self.y_min2 = y_min
        
    def plotting(self): #Erstellt den Graphen
        if 'y1' in self.which_y_axis:
            self.x_y_lim_y1(self.x_in, self.y_in) 
        xlim = [self.x_min, self.x_max]
        ylim = [self.y_min, self.y_max]
        if 'y2' in self.which_y_axis:
            self.x_y_lim_y2(self.x_in, self.y_in)
        ylim2 = [self.y_min2, self.y_max2]
        
        fig, ax1 = plt.subplots()

        if self.BOOL_1Graph == False:
            for i in range(len(self.x_in)):
                if self.which_y_axis[i] == 'y1':
                    x_plot = np.array(self.x_in[i]).T
                    y_plot = np.array(self.y_in[i]).T
                    plt.plot(x_plot, y_plot,self.linestyle[i],color=self.color[i] ,label=self.label[i])
        else:
            plt.plot(self.x_in, self.y_in,self.linestyle,color=self.color ,label=self.label)
        plt.xlabel(self.x_axe_name, fontdict=self.font)
        plt.ylabel(self.y_axe_name, fontdict=self.font)
        plt.ticklabel_format(axis='y', style='plain')
        if self.log_y == True:
            plt.yscale('log')
        else:
            plt.yscale('linear')
            plt.ticklabel_format(axis='x', style='sci', scilimits=(-2,2))
        if self.log_x == True:
            plt.xscale('log')
        else:
            plt.xscale('linear')
            plt.ticklabel_format(axis='x', style='sci', scilimits=(-2,2))
        plt.xticks(fontname = self.fontfamily,size=14)
        plt.yticks(fontname = self.fontfamily,size=14)
        
        if 'y2' in self.which_y_axis:
            plt.tick_params(axis='both',direction='in', labelsize = 14, length=6, width=2, which='both', colors='black',grid_color='r', grid_alpha=0.5, bottom=True, top=True, left=True, right=False)
        else:
            plt.tick_params(axis='both',direction='in', labelsize = 14, length=6, width=2, which='both', colors='black',grid_color='r', grid_alpha=0.5, bottom=True, top=True, left=True, right=True)
        plt.tick_params(which='minor', length=4, color='black')
        plt.tick_params(axis='both', which='minor', labelsize=0, length = 4)
        ax1.set_xlim(xlim[0], xlim[1])
        ax1.set_ylim(ylim[0], ylim[1])
        
        if 'y2' in self.which_y_axis:
            ax2 = ax1.twinx()
            ax2.set_ylim(ylim2[0], ylim2[1])
            plt.tick_params(axis='both',direction='in', labelsize = 14, length=6, width=2, which='both', colors='black',grid_color='r', grid_alpha=0.5, bottom=True, top=True, left=False, right=True)
            if self.log_y2 == True:   
                plt.yscale('log')
            else:
                plt.yscale('linear')
                plt.ticklabel_format(axis='y', style=self.y2_style, scilimits=(-2,2))
        
        if 'y2' in self.which_y_axis:
            for i in range(len(self.x_in)):
                if self.which_y_axis[i] == 'y2':
                    x_plot = np.array(self.x_in[i]).T
                    y_plot = np.array(self.y_in[i]).T
                    plt.plot(x_plot, y_plot,self.linestyle[i],color=self.color[i] ,label=self.label[i])
                    ax2.set_ylabel(self.y2_axe_name, fontdict=self.font)
        
        if 'y2' in self.which_y_axis:
            # ask matplotlib for the plotted objects and their labels
            lines, labels = ax1.get_legend_handles_labels()
            lines2, labels2 = ax2.get_legend_handles_labels()
            ax2.legend(lines + lines2, labels + labels2, loc=self.legend_loc, prop={'family': self.fontfamily,'size': 10})
        else:
            plt.legend(loc=self.legend_loc, prop={'family': self.fontfamily,'size': 10})
            
        if self.save_plotting == True:
            plt.savefig(self.fig_Name + '.png', dpi=500, bbox_inches='tight')
            pngname = self.fig_Name + '.png'
            pdfname = self.fig_Name + '.pdf'
            image1 = Image.open(pngname)
            im1 = image1.convert('RGB')
            im1.save(pdfname)
        plt.show()


 
