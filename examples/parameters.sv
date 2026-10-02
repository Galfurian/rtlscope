// One parameterized module instantiated at two widths.

module bit_reverse #(
    parameter int WIDTH = 8
) (
    input  logic [WIDTH-1:0] d,
    output logic [WIDTH-1:0] q
);
  always_comb begin
    for (int i = 0; i < WIDTH; i++) q[i] = d[WIDTH-1-i];
  end
endmodule

module parameters (
    input  logic [3:0] d4,
    input  logic [7:0] d8,
    output logic [3:0] q4,
    output logic [7:0] q8
);
  bit_reverse #(.WIDTH(4)) u4 (.d(d4), .q(q4));
  bit_reverse              u8 (.d(d8), .q(q8));
endmodule
