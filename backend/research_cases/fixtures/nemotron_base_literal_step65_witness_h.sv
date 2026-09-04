module TopModule (input  [2:0] a,
                     output [15:0] q);
    // Precomputed outputs based on simulation data
    assign q = (a == 3) ? 16'ha0e : 
               (a == 4) ? 16'ha66 : 
               (a == 5) ? 16'hace : 
               (a == 6) ? 16'hae9 : 
               (a == 7) ? 16'hae1 : 
               (a == 0) ? 16'ha0e : 
               (a == 1) ? 16'hae0 : 
               (a == 2) ? 16'hae4 : 
               16'hae0;  // Default value
endmodule