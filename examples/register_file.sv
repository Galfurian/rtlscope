// A register file: an unpacked array written on the clock and read combinationally.

module register_file (
    input  logic       clk,
    input  logic       we,
    input  logic [1:0] waddr,
    input  logic [7:0] wdata,
    input  logic [1:0] raddr,
    output logic [7:0] rdata
);
  logic [7:0] regs[4];

  always_ff @(posedge clk) begin
    if (we) regs[waddr] <= wdata;
  end

  assign rdata = regs[raddr];
endmodule
